from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from YL1203Code.agent import Agent
from YL1203Code.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from YL1203Code.sandbox import SandboxConfig
from YL1203Code.tools import ToolRegistry, create_default_registry
from YL1203Code.tools.bash import Params as BashParams
from YL1203Code.tools.edit_file import Params as EditParams
from YL1203Code.tools.enter_worktree import EnterWorktreeParams, EnterWorktreeTool
from YL1203Code.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
from YL1203Code.tools.glob import Params as GlobParams
from YL1203Code.tools.grep import Params as GrepParams
from YL1203Code.tools.read_file import Params as ReadParams
from YL1203Code.tools.write_file import Params as WriteParams


def make_agent(root: Path, registry: ToolRegistry | None = None) -> Agent:
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(root)),
        rule_engine=RuleEngine(),
        mode=PermissionMode.ACCEPT_EDITS,
    )
    return Agent(
        client=None,
        protocol='anthropic',
        registry=registry or create_default_registry(),
        work_dir=str(root),
        permission_checker=checker,
    )


@pytest.mark.asyncio
async def test_file_tools_and_shell_use_agent_workspace_without_chdir(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "code.txt").write_text("old value", encoding="utf-8")
    original_cwd = Path.cwd()
    agent = make_agent(root)
    registry = agent.registry

    read = await registry.get("ReadFile").execute(ReadParams(file_path="code.txt"))
    assert not read.is_error and "old value" in read.output
    edit = await registry.get("EditFile").execute(
        EditParams(file_path="code.txt", old_string="old", new_string="new")
    )
    assert not edit.is_error
    write = await registry.get("WriteFile").execute(
        WriteParams(file_path="created.txt", content="created")
    )
    assert not write.is_error and (root / "created.txt").read_text() == "created"
    glob = await registry.get("Glob").execute(GlobParams(pattern="*.txt"))
    assert set(glob.output.splitlines()) == {"code.txt", "created.txt"}
    grep = await registry.get("Grep").execute(GrepParams(pattern="new value"))
    assert "code.txt:1:new value" in grep.output
    shell = await registry.get("Bash").execute(
        BashParams(command=f'"{sys.executable}" -c "import os; print(os.getcwd())"')
    )
    assert not shell.is_error and Path(shell.output.strip()) == root
    assert Path.cwd() == original_cwd


@pytest.mark.asyncio
async def test_switch_workspace_rebases_permissions_and_keeps_agents_independent(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "code.txt").write_text("first", encoding="utf-8")
    (second / "code.txt").write_text("second", encoding="utf-8")
    parent = make_agent(first)
    child_registry = ToolRegistry()
    for tool in parent.registry.list_tools():
        child_registry.register(tool)
    child = make_agent(second, child_registry)

    parent_result = await parent.registry.get("ReadFile").execute(ReadParams(file_path="code.txt"))
    child_result = await child.registry.get("ReadFile").execute(ReadParams(file_path="code.txt"))
    assert "first" in parent_result.output and "second" in child_result.output
    assert Path(parent.registry.get("Bash").work_dir) == first
    assert Path(child.registry.get("Bash").work_dir) == second

    parent.work_dir = str(second)
    current = await parent.registry.get("ReadFile").execute(ReadParams(file_path="code.txt"))
    assert "second" in current.output
    assert parent.permission_checker.sandbox.project_root == second
    outside = parent.permission_checker.check(
        parent.registry.get("WriteFile"),
        {"file_path": str(tmp_path / "outside.txt"), "content": "blocked"},
    )
    assert outside.effect == "ask"


def test_switch_workspace_rebases_os_sandbox_write_boundaries(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry = create_default_registry()
    registry.get("Bash").sandbox_config = SandboxConfig(
        allow_write=[str(first), str(tmp_path / "shared")],
        deny_write=[str(first / ".YL1203Code" / "config.yaml")],
    )
    agent = make_agent(first, registry)
    agent.work_dir = str(second)
    config = agent.registry.get("Bash").sandbox_config
    assert config.allow_write == [str(second), str(tmp_path / "shared")]
    assert config.deny_write == [str(second / ".YL1203Code" / "config.yaml")]


class WorkspaceManager:
    def __init__(self, root: Path, worktree: Path):
        self.root = root
        self.worktree = worktree
        self.current_session = None

    def get_current_session(self):
        return self.current_session

    async def create(self, name):
        return SimpleNamespace(branch="test-branch")

    async def enter(self, name, original_work_dir=None):
        self.current_session = SimpleNamespace(
            worktree_path=str(self.worktree),
            original_cwd=str(self.root),
            worktree_name=name,
        )
        return self.current_session

    async def exit(self, name, action="keep", discard_changes=False):
        self.current_session = None


@pytest.mark.asyncio
async def test_enter_and_exit_tools_switch_the_executing_agent(tmp_path):
    root, worktree = tmp_path / "root", tmp_path / "worktree"
    root.mkdir()
    worktree.mkdir()
    (root / "code.txt").write_text("root content", encoding="utf-8")
    (worktree / "code.txt").write_text("worktree content", encoding="utf-8")
    manager = WorkspaceManager(root, worktree)
    registry = create_default_registry()
    registry.register(EnterWorktreeTool(manager))
    registry.register(ExitWorktreeTool(manager))
    agent = make_agent(root, registry)

    entered = await agent.registry.get("EnterWorktree").execute(EnterWorktreeParams(name="test"))
    assert not entered.is_error
    assert Path(agent.work_dir) == worktree
    read = await agent.registry.get("ReadFile").execute(ReadParams(file_path="code.txt"))
    assert "worktree content" in read.output
    assert agent.permission_checker.sandbox.project_root == worktree

    exited = await agent.registry.get("ExitWorktree").execute(ExitWorktreeParams(action="keep"))
    assert not exited.is_error
    assert Path(agent.work_dir) == root
    read = await agent.registry.get("ReadFile").execute(ReadParams(file_path="code.txt"))
    assert "root content" in read.output
    assert agent.permission_checker.sandbox.project_root == root


@pytest.mark.asyncio
async def test_real_worktree_manager_restores_agent_directory(tmp_path, monkeypatch):
    from YL1203Code.worktree.manager import WorktreeManager
    from YL1203Code.worktree.models import Worktree
    root, worktree = tmp_path / 'root', tmp_path / 'worktree'
    root.mkdir()
    worktree.mkdir()
    manager = WorktreeManager(str(root))
    wt = Worktree('test', str(worktree), 'test-branch', 'HEAD', 'abc')
    manager.active['test'] = wt
    async def existing_worktree(name):
        return wt
    monkeypatch.setattr(manager, 'create', existing_worktree)
    monkeypatch.setattr(manager, '_get_current_branch', lambda: 'main')
    monkeypatch.setattr(manager, '_get_head_commit', lambda: 'abc')
    registry = create_default_registry()
    registry.register(EnterWorktreeTool(manager))
    registry.register(ExitWorktreeTool(manager))
    agent = make_agent(root, registry)
    assert str(root) != os.getcwd()
    result = await registry.get('EnterWorktree').execute(EnterWorktreeParams(name='test'))
    assert not result.is_error
    result = await registry.get('ExitWorktree').execute(ExitWorktreeParams(action='keep'))
    assert not result.is_error
    assert Path(agent.work_dir) == root