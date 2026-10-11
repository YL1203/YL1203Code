"""Shared core initialization for terminal, browser and prompt entrypoints."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from YL1203Code.agent import Agent
from YL1203Code.agents.loader import AgentLoader
from YL1203Code.agents.task_manager import TaskManager
from YL1203Code.agents.trace import TraceManager
from YL1203Code.client import LLMClient, create_client
from YL1203Code.config import AppConfig
from YL1203Code.filehistory import FileHistory
from YL1203Code.hooks import HookEngine
from YL1203Code.memory import MemoryManager, load_instructions
from YL1203Code.memory.session import Session, SessionManager
from YL1203Code.permissions import (
    DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine,
)
from YL1203Code.sandbox import SandboxConfig, create_sandbox
from YL1203Code.skills.executor import SkillExecutor
from YL1203Code.skills.loader import SkillLoader
from YL1203Code.teams.manager import TeamManager
from YL1203Code.tools import ToolRegistry, create_default_registry
from YL1203Code.tools.agent_tool import AgentTool
from YL1203Code.tools.enter_worktree import EnterWorktreeTool
from YL1203Code.tools.exit_worktree import ExitWorktreeTool
from YL1203Code.tools.impl.tool_search import ToolSearchTool
from YL1203Code.tools.load_skill import LoadSkill
from YL1203Code.tools.synthetic_output import SyntheticOutputTool
from YL1203Code.tools.team_create import TeamCreateTool
from YL1203Code.tools.team_delete import TeamDeleteTool
from YL1203Code.worktree import WorktreeManager


@dataclass
class Runtime:
    agent: Agent
    registry: ToolRegistry
    memory_manager: MemoryManager
    session_manager: SessionManager
    session: Session
    file_history: FileHistory
    skill_loader: SkillLoader
    skill_executor: SkillExecutor
    agent_loader: AgentLoader
    task_manager: TaskManager
    trace_manager: TraceManager
    worktree_manager: WorktreeManager
    team_manager: TeamManager
    warnings: list[str] = field(default_factory=list)

    async def close(self) -> None:
        tasks = list(self.task_manager._async_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.session.close()


def create_runtime(
    config: AppConfig, permission_mode: PermissionMode, work_dir: str,
    *, client: LLMClient | None = None, registry: ToolRegistry | None = None,
    hook_engine: HookEngine | None = None, task_manager: TaskManager | None = None,
    trace_manager: TraceManager | None = None, is_interactive: bool = False,
) -> Runtime:
    root = Path(work_dir).resolve()
    (root / ".YL1203Code").mkdir(parents=True, exist_ok=True)
    provider = config.providers[0]
    client = client or create_client(provider)
    registry = registry or create_default_registry()
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(root)),
        RuleEngine(
            user_rules_path=Path.home() / ".YL1203Code" / "permissions.yaml",
            project_rules_path=root / ".YL1203Code" / "permissions.yaml",
            local_rules_path=root / ".YL1203Code" / "permissions.local.yaml",
        ), permission_mode,
    )
    warnings: list[str] = []
    bash = registry.get("Bash")
    if bash:
        bash.sandbox = None
        bash.sandbox_config = None
    if config.sandbox.enabled:
        sandbox = create_sandbox()
        if sandbox is not None and sandbox.available() and bash:
            bash.sandbox = sandbox
            bash.sandbox_config = SandboxConfig(
                allow_write=[str(root), tempfile.gettempdir()],
                deny_write=[str(root / ".YL1203Code" / name) for name in (
                    "config.yaml", "config.local.yaml", "permissions.local.yaml", "skills",
                )], network_enabled=config.sandbox.network_enabled,
            )
            checker.sandbox_enabled = config.sandbox.auto_allow
        else:
            warnings.append("OS command sandbox is unavailable; permission checks remain enabled.")

    memory_manager = MemoryManager(str(root))
    session_manager = SessionManager(str(root))
    session = session_manager.create()
    file_history = FileHistory(str(root), session.session_id)
    for tool in registry.list_tools():
        if hasattr(tool, "file_history"):
            tool.file_history = file_history
    registry.register(ToolSearchTool(registry, provider.protocol))
    load_skill = LoadSkill()
    registry.register(load_skill)
    agent = Agent(
        client, registry, provider.protocol, work_dir=str(root),
        permission_checker=checker, context_window=provider.get_context_window(),
        instructions_content=load_instructions(str(root)), memory_manager=memory_manager,
        hook_engine=hook_engine, max_iterations=config.execution.max_iterations,
        max_total_tokens=config.execution.max_total_tokens,
        timeout_seconds=config.execution.timeout_seconds,
    )
    agent.file_history = file_history
    agent.session_id = session.session_id
    skill_loader = SkillLoader(str(root))
    skill_loader.load_all()
    load_skill.set_loader(skill_loader)
    load_skill.set_agent(agent)
    catalog = skill_loader.get_catalog()
    if catalog:
        agent.set_skill_catalog("\n".join([
            "You can use the following Skills:",
            *(f"- {name}: {description}" for name, description in catalog),
            "Call LoadSkill to activate a matching skill.",
        ]))
    skill_executor = SkillExecutor(agent, client, provider.protocol)

    worktree_manager = WorktreeManager(
        str(root), symlink_directories=config.worktree.symlink_directories,
    )
    restored = worktree_manager.restore_session()
    if restored:
        agent.work_dir = restored.worktree_path
    registry.register(EnterWorktreeTool(worktree_manager))
    registry.register(ExitWorktreeTool(worktree_manager))
    task_manager = task_manager or TaskManager()
    task_manager.configure_storage(str(root))
    trace_manager = trace_manager or TraceManager()
    agent_loader = AgentLoader(str(root), enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=worktree_manager, trace_manager=trace_manager)
    registry.register(AgentTool(
        agent_loader, task_manager, trace_manager, agent,
        enable_fork=config.enable_fork, provider_config=provider,
        worktree_manager=worktree_manager, team_manager=team_manager,
    ))
    registry.register(TeamCreateTool(
        team_manager, agent, teammate_mode=config.teammate_mode or "in-process",
        is_interactive=is_interactive, enable_coordinator_mode=config.enable_coordinator_mode,
    ))
    registry.register(TeamDeleteTool(team_manager, agent))
    registry.register(SyntheticOutputTool())
    agent._team_manager = team_manager
    def drain_notifications() -> list[str]:
        notes = [
            f"<task-notification><task_id>{task.id}</task_id>"
            f"<status>{task.status}</status><result>{task.result}</result></task-notification>"
            for task in task_manager.poll_completed()
        ]
        notes.extend(team_manager.drain_lead_mailbox())
        return notes
    agent.notification_fn = drain_notifications
    agent_catalog = agent_loader.list_agents()
    if agent_catalog:
        agent.set_agent_catalog("\n".join([
            "## Available Sub-Agent Types",
            *(f"- **{name}**: {description}" for name, description in agent_catalog),
            "Use Agent to delegate. Background results arrive as task notifications.",
        ]), catalog_list=agent_catalog)
    return Runtime(
        agent, registry, memory_manager, session_manager, session, file_history,
        skill_loader, skill_executor, agent_loader, task_manager, trace_manager,
        worktree_manager, team_manager, warnings,
    )


def register_runtime_commands(runtime: Runtime, registry: Any) -> None:
    from YL1203Code.commands.handlers.tasks import create_tasks_command
    from YL1203Code.commands.handlers.trace import create_trace_command
    from YL1203Code.commands.handlers.worktree import create_worktree_command
    from YL1203Code.commands.handlers.skill_register import register_skill_commands

    for command in (
        create_worktree_command(runtime.worktree_manager),
        create_tasks_command(runtime.task_manager),
        create_trace_command(runtime.trace_manager, runtime.agent.agent_id),
    ):
        if registry.find(command.name) is None:
            registry.register_sync(command)
    register_skill_commands(registry, runtime.skill_loader, runtime.skill_executor)
