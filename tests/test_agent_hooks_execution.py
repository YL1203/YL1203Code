from __future__ import annotations

import asyncio

import pytest

from YL1203Code.hooks import HookEngine, HookContext, Action, Hook
from YL1203Code.hooks.executors import execute_agent
from YL1203Code.tools.base import StreamEnd, TextDelta, ToolCallComplete
from test_skill_fork_execution import ScriptClient, make_agent


@pytest.mark.asyncio
async def test_agent_hook_reads_workspace_without_recursing(tmp_path):
    (tmp_path / 'answer.txt').write_text('The answer is 42')
    class ReaderClient:
        def __init__(self):
            self.calls = 0
        async def stream(self, conversation, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert 'Inspect answer.txt' in '\n'.join(m.content for m in conversation.history)
                yield ToolCallComplete('r', 'ReadFile', {'file_path': 'answer.txt'})
            else:
                assert any('The answer is 42' in r.content for m in conversation.history for r in m.tool_results)
                yield TextDelta('Verified 42')
            yield StreamEnd('end_turn')
    client = ReaderClient()
    agent = make_agent(tmp_path, client)
    engine = HookEngine([Hook('inspect', 'pre_tool_use', Action(type='agent', prompt='Inspect $FILE_PATH'))])
    agent.hook_engine = engine
    await engine.run_hooks('pre_tool_use', agent._build_hook_context('pre_tool_use', file_path='answer.txt'))
    notices = engine.drain_notifications()
    assert len(notices) == 1 and notices[0].success
    assert notices[0].output == 'Verified 42'
    assert client.calls == 2


@pytest.mark.asyncio
async def test_agent_hook_cannot_write(tmp_path):
    client = ScriptClient([ToolCallComplete('w', 'WriteFile', {'file_path': 'unexpected.txt', 'content': 'bad'})])
    agent = make_agent(tmp_path, client)
    result = await execute_agent(Action(type='agent', prompt='Write a file'), agent._build_hook_context('turn_start'))
    assert not (tmp_path / 'unexpected.txt').exists()
    assert not result.success


@pytest.mark.asyncio
async def test_agent_hook_timeout_cancels_runner():
    cancelled = asyncio.Event()
    async def slow_runner(prompt):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    ctx = HookContext()
    ctx.agent_runner = slow_runner
    result = await execute_agent(Action(type='agent', prompt='Check', timeout=0.01), ctx)
    assert not result.success
    assert cancelled.is_set()