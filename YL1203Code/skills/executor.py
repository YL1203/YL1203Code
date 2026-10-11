 #YL1203Code
from __future__ import annotations

import logging
import copy
from typing import TYPE_CHECKING

from YL1203Code.conversation import ConversationManager, Message
from YL1203Code.skills.parser import SkillDef, substitute_arguments

if TYPE_CHECKING:
    from YL1203Code.agent import Agent, AgentEvent
    from YL1203Code.client import LLMClient

log = logging.getLogger(__name__)

FORK_RECENT_COUNT = 5


class SkillExecutor:


    def __init__(
        self,
        agent: Agent,
        client: LLMClient,
        protocol: str,
    ) -> None:
        self.agent = agent
        self.client = client
        self.protocol = protocol


    def execute_inline(self, skill: SkillDef, args: str) -> None:
        prompt = substitute_arguments(skill.prompt_body, args)
        self.agent.activate_skill(skill.name, prompt)
        if getattr(self.agent, "recovery_state", None) is not None:
            self.agent.recovery_state.record_skill_invocation(skill.name, prompt)


    async def execute_fork(
        self, skill: SkillDef, args: str
    ) -> str:
        prompt = substitute_arguments(skill.prompt_body, args)
        if getattr(self.agent, "recovery_state", None) is not None:
            self.agent.recovery_state.record_skill_invocation(
                skill.name, skill.prompt_body
            )

        fork_conv = ConversationManager()

        context_messages = self._build_fork_context(skill.context)
        for msg in context_messages:
            if msg.role == "user":
                fork_conv.add_user_message(msg.content)
            else:
                fork_conv.add_assistant_message(msg.content)

        fork_conv.add_user_message(prompt)

        from YL1203Code.agent import (
            Agent as AgentClass, StreamText, LoopComplete, ErrorEvent,
            PermissionRequest, PermissionResponse,
        )
        from YL1203Code.agents.tool_filter import ALL_AGENT_DISALLOWED_TOOLS

        registry = self.agent.registry.copy()
        # A skill fork cannot control the parent's UI, teams or worktree session.
        for name in ALL_AGENT_DISALLOWED_TOOLS | {
            "TeamCreate", "TeamDelete", "EnterWorktree", "ExitWorktree",
        }:
            registry.disable(name)
        checker = self.agent.permission_checker

        fork_agent = AgentClass(
            client=self.client,
            registry=registry,
            protocol=self.protocol,
            work_dir=self.agent.work_dir,
            max_iterations=self.agent.max_iterations,
            permission_checker=checker.copy() if checker else None,
            context_window=self.agent.context_window,
            max_total_tokens=self.agent.max_total_tokens,
            timeout_seconds=self.agent.timeout_seconds,
            instructions_content=self.agent.instructions_content,
        )
        fork_agent.parent_id = self.agent.agent_id
        fork_agent.trace_id = self.agent.trace_id or self.agent.agent_id
        fork_agent.active_skills = dict(self.agent.active_skills)
        fork_agent.set_skill_catalog(self.agent._skill_catalog)
        loader = registry.get("LoadSkill")
        if loader is not None:
            loader = copy.copy(loader)
            loader.set_agent(fork_agent)
            registry.register(loader)

        result_parts: list[str] = []
        async for event in fork_agent.run(fork_conv):
            if isinstance(event, StreamText):
                result_parts.append(event.text)
            elif isinstance(event, ErrorEvent):
                result_parts.append(f"\n[Error: {event.message}]")
            elif isinstance(event, PermissionRequest):
                if not event.future.done():
                    event.future.set_result(PermissionResponse.DENY)
            elif isinstance(event, LoopComplete):
                break

        return "".join(result_parts)


    def _build_fork_context(self, mode: str) -> list[Message]:
        if mode == "none":
            return []

        conversation = getattr(self.agent, "_current_conversation", None)
        history = conversation.history if conversation is not None else []
        if not history:
            main_history = []
        else:
            main_history = history

        if mode == "recent":
            content_messages = [
                m for m in main_history
                if m.content and not m.tool_results
            ]
            return content_messages[-FORK_RECENT_COUNT:]

        if mode == "full":
            content_messages = [
                m for m in main_history
                if m.content and not m.tool_results
            ]
            if not content_messages:
                return []
            summary_parts = []
            for m in content_messages:
                prefix = "User" if m.role == "user" else "Assistant"
                text = m.content[:200]
                if len(m.content) > 200:
                    text += "..."
                summary_parts.append(f"{prefix}: {text}")
            summary = "## Previous conversation summary\n\n" + "\n\n".join(summary_parts)
            return [Message(role="user", content=summary)]

        return []
