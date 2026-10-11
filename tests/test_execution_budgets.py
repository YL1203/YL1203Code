from __future__ import annotations

import asyncio

import pytest

from YL1203Code.agent import Agent, ErrorEvent, LoopComplete
from YL1203Code.conversation import ConversationManager
from YL1203Code.tools import create_default_registry
from YL1203Code.tools.base import StreamEnd, ToolCallComplete


class LoopingClient:
    def __init__(self):
        self.calls = 0
    async def stream(self, *args, **kwargs):
        self.calls += 1
        yield ToolCallComplete(str(self.calls), 'ReadFile', {'file_path': 'code.txt'})
        yield StreamEnd('end_turn', input_tokens=20, output_tokens=10)


@pytest.mark.asyncio
async def test_streaming_iteration_limit_is_reported(tmp_path):
    (tmp_path / 'code.txt').write_text('code')
    client = LoopingClient()
    agent = Agent(client, create_default_registry(), 'anthropic', work_dir=str(tmp_path), max_iterations=1)
    conv = ConversationManager()
    conv.add_user_message('Keep reading')
    events = [e async for e in agent.run(conv)]
    assert client.calls == 1
    assert any(isinstance(e, ErrorEvent) and 'limit' in e.message.lower() for e in events)
    assert events[-1].stop_reason == 'iteration_limit'


@pytest.mark.asyncio
async def test_noninteractive_iteration_limit_is_a_failure(tmp_path):
    (tmp_path / 'code.txt').write_text('code')
    client = LoopingClient()
    agent = Agent(client, create_default_registry(), 'anthropic', work_dir=str(tmp_path), max_iterations=1)
    with pytest.raises(RuntimeError, match='limit'):
        await agent.run_to_completion('Keep reading')
    assert client.calls == 1


@pytest.mark.asyncio
async def test_token_budget_stops_another_model_call(tmp_path):
    client = LoopingClient()
    agent = Agent(client, create_default_registry(), 'anthropic', work_dir=str(tmp_path), max_total_tokens=25)
    conv = ConversationManager()
    conv.add_user_message('Keep reading')
    events = [e async for e in agent.run(conv)]
    assert client.calls == 1
    assert events[-1].stop_reason == 'token_limit'


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [True, False])
async def test_task_timeout_cancels_model_stream(tmp_path, streaming):
    cancelled = asyncio.Event()
    class SlowClient:
        async def stream(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            yield
    agent = Agent(SlowClient(), create_default_registry(), 'anthropic', work_dir=str(tmp_path), timeout_seconds=0.01)
    if streaming:
        conv = ConversationManager()
        conv.add_user_message('Wait')
        events = [e async for e in agent.run(conv)]
        assert events[-1].stop_reason == 'timeout'
    else:
        with pytest.raises(RuntimeError, match='time'):
            await agent.run_to_completion('Wait')
    assert cancelled.is_set()

def test_execution_config_validation_and_layer_override(tmp_path):
    from YL1203Code.config import _load_single_file, _merge_config
    from YL1203Code.validator import ConfigError
    base_file, override_file = tmp_path / 'base.yaml', tmp_path / 'override.yaml'
    providers = 'providers:\n  - name: test\n    protocol: anthropic\n    base_url: https://example.invalid\n    model: test\n'
    base_file.write_text(providers + 'execution:\n  max_iterations: 4\n  max_total_tokens: 200\n')
    override_file.write_text(providers + 'execution:\n  max_total_tokens: 0\n  timeout_seconds: 30\n')
    merged = _merge_config(_load_single_file(base_file), _load_single_file(override_file))
    assert merged.execution.max_iterations == 4
    assert merged.execution.max_total_tokens == 0
    assert merged.execution.timeout_seconds == 30
    override_file.write_text(providers + 'execution:\n  max_iterations: -1\n')
    with pytest.raises(ConfigError, match='execution'):
        _load_single_file(override_file)

@pytest.mark.asyncio
async def test_task_timeout_stops_command_descendants(tmp_path):
    import shlex
    import subprocess
    import sys
    from YL1203Code.tools.bash import Bash, Params
    started = tmp_path / 'started'
    late = tmp_path / 'late'
    script = tmp_path / 'delayed.py'
    script.write_text("from pathlib import Path\nimport time\nPath('started').write_text('started')\ntime.sleep(3)\nPath('late').write_text('escaped')\n")
    args = [sys.executable, str(script)]
    command = subprocess.list2cmdline(args) if sys.platform == 'win32' else shlex.join(args)
    tool = Bash()
    tool.work_dir = str(tmp_path)
    task = asyncio.create_task(tool.execute(Params(command=command)))
    for _ in range(100):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(3.2)
    assert not late.exists()
