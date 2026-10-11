from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from YL1203Code.tools.base import ToolCallComplete, ToolResult

if TYPE_CHECKING:
    from YL1203Code.agent import Agent, PermissionRequest


@dataclass
class TestRunResult:
    passed: bool
    attempts: int
    output: str


class TestWorkflow:
    __test__ = False

    def __init__(self, agent: Agent, *, permission_handler:
                 Callable[[PermissionRequest], Awaitable[None]] | None = None) -> None:
        self.agent = agent
        self.permission_handler = permission_handler
        self.attempts = 0

    async def _permission(self, request: PermissionRequest) -> None:
        from YL1203Code.agent import PermissionResponse
        if self.permission_handler:
            await self.permission_handler(request)
        elif not request.future.done():
            request.future.set_result(PermissionResponse.DENY)

    async def run(self, command: str, *, repair: bool = False, max_attempts: int = 3) -> TestRunResult:
        if not command.strip() or not 1 <= max_attempts <= 3:
            raise ValueError('Provide a test command and 1-3 attempts')
        self.attempts = 0
        try:
            async with asyncio.timeout(self.agent.timeout_seconds or None):
                return await self._run(command, repair, max_attempts)
        except TimeoutError:
            return TestRunResult(False, self.attempts, 'Test workflow stopped: timeout')

    async def _run(self, command: str, repair: bool, max_attempts: int) -> TestRunResult:
        from YL1203Code.agent import Agent, ErrorEvent, PermissionRequest
        from YL1203Code.conversation import ConversationManager

        repair_tokens = 0
        for attempt in range(1, max_attempts + 1):
            self.attempts = attempt
            result = ToolResult('Test did not execute', is_error=True)
            stream = self.agent._execute_tool(
                ToolCallComplete(f'test-{attempt}', 'Bash', {'command': command}),
            )
            async with aclosing(stream):
                async for event in stream:
                    if isinstance(event, PermissionRequest):
                        await self._permission(event)
                    else:
                        result = event[0]
            if not result.is_error and result.exit_code in (None, 0):
                return TestRunResult(True, attempt, result.output)
            if not repair or attempt == max_attempts or result.output.startswith('Permission denied'):
                return TestRunResult(False, attempt, result.output)
            remaining = self.agent.max_total_tokens - repair_tokens if self.agent.max_total_tokens else 0
            if self.agent.max_total_tokens and remaining <= 0:
                return TestRunResult(False, attempt, result.output + '\nRepair stopped: token_limit')
            registry = self.agent.registry.copy()
            for tool in registry.list_tools():
                if tool.name not in {'ReadFile', 'WriteFile', 'EditFile', 'Glob', 'Grep', 'Bash'}:
                    registry.disable(tool.name)
            checker = self.agent.permission_checker
            worker = Agent(
                self.agent.client, registry, self.agent.protocol, work_dir=self.agent.work_dir,
                permission_checker=checker.copy() if checker else None,
                instructions_content=self.agent.instructions_content,
                max_iterations=min(self.agent.max_iterations or 10, 10),
                max_total_tokens=remaining, timeout_seconds=self.agent.timeout_seconds,
                context_window=self.agent.context_window,
            )
            worker.file_history = self.agent.file_history
            conversation = ConversationManager()
            conversation.add_user_message(
                "Fix the code causing this test failure. Preserve the test's intended behavior; "
                'do not delete or weaken assertions. The caller will rerun the tests.\n'
                f'Command: {command}\nOutput:\n{result.output}',
            )
            async with aclosing(worker.run(conversation)) as stream:
                async for event in stream:
                    if isinstance(event, PermissionRequest):
                        await self._permission(event)
                    elif isinstance(event, ErrorEvent):
                        return TestRunResult(False, attempt, f'{result.output}\nRepair stopped: {event.message}')
            repair_tokens += worker.total_input_tokens + worker.total_output_tokens
        raise AssertionError('Unreachable')
