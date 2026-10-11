from __future__ import annotations

import copy
from pathlib import Path

import pytest

from YL1203Code.memory.consolidation import MemoryConsolidator, _try_acquire_lock, _lock_path
from YL1203Code.tools.base import StreamEnd, TextDelta, ToolCallComplete


class ScriptClient:
    def __init__(self, calls):
        self.calls = calls
        self.seen = []

    async def stream(self, conversation, **kwargs):
        self.seen.append(copy.deepcopy(conversation.history))
        if len(self.seen) == 1:
            for call in self.calls:
                yield call
        else:
            yield TextDelta('Consolidated memories.')
        yield StreamEnd('end_turn')


def setup_consolidator(tmp_path, monkeypatch):
    user_home = tmp_path / 'user'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: user_home))
    project = tmp_path / 'project'
    project.mkdir()
    consolidator = MemoryConsolidator(str(project))
    for root in (consolidator._mem_dir, consolidator._user_mem_dir):
        Path(root).mkdir(parents=True)
    return consolidator, project


@pytest.mark.asyncio
async def test_production_consolidation_updates_both_memory_roots(tmp_path, monkeypatch):
    consolidator, project = setup_consolidator(tmp_path, monkeypatch)
    index = Path(consolidator._mem_dir) / 'MEMORY.md'
    index.write_text('Old index')
    stale = Path(consolidator._mem_dir) / 'stale.md'
    stale.write_text('Contradicted fact')
    user_memory = Path(consolidator._user_mem_dir) / 'preference.md'
    client = ScriptClient([
        ToolCallComplete('r1', 'ReadFile', {'file_path': str(index)}),
        ToolCallComplete('w1', 'WriteFile', {'file_path': str(index), 'content': 'Updated index'}),
        ToolCallComplete('w2', 'WriteFile', {'file_path': str(user_memory), 'content': 'Keep useful facts'}),
        ToolCallComplete('r2', 'ReadFile', {'file_path': str(stale)}),
        ToolCallComplete('d', 'DeleteMemory', {'file_path': str(stale)}),
    ])
    await consolidator._do_consolidation(client, None, 'anthropic', ['session-1'])
    assert index.read_text() == 'Updated index'
    assert user_memory.read_text() == 'Keep useful facts'
    assert not stale.exists()
    results = [r for m in client.seen[-1] for r in m.tool_results]
    assert all(not r.is_error for r in results)


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['app.py', '.YL1203Code/sessions/session.jsonl',
                                  '.YL1203Code/memory/../../app.py'])
async def test_consolidation_cannot_write_outside_memory(tmp_path, monkeypatch, target):
    consolidator, project = setup_consolidator(tmp_path, monkeypatch)
    client = ScriptClient([ToolCallComplete('w', 'WriteFile',
                          {'file_path': target, 'content': 'unexpected'})])
    await consolidator._do_consolidation(client, None, 'anthropic', [])
    assert not (project / target).exists()
    results = [r for m in client.seen[-1] for r in m.tool_results]
    assert results and results[0].is_error


@pytest.mark.asyncio
async def test_consolidation_delete_requires_read(tmp_path, monkeypatch):
    consolidator, project = setup_consolidator(tmp_path, monkeypatch)
    stale = Path(consolidator._mem_dir) / 'stale.md'
    stale.write_text('Useful fact')
    client = ScriptClient([ToolCallComplete('d', 'DeleteMemory', {'file_path': str(stale)})])
    await consolidator._do_consolidation(client, None, 'anthropic', [])
    assert stale.read_text() == 'Useful fact'
    results = [r for m in client.seen[-1] for r in m.tool_results]
    assert results[0].is_error


@pytest.mark.asyncio
async def test_failed_consolidation_releases_lock(tmp_path, monkeypatch):
    consolidator, _ = setup_consolidator(tmp_path, monkeypatch)
    class FailingClient:
        async def stream(self, *args, **kwargs):
            raise RuntimeError('provider unavailable')
            yield
    prior = _try_acquire_lock(consolidator._mem_dir)
    await consolidator._run(FailingClient(), None, 'anthropic', [], prior)
    assert not Path(_lock_path(consolidator._mem_dir)).exists()

@pytest.mark.asyncio
async def test_session_gate_starts_real_consolidation(tmp_path, monkeypatch):
    import asyncio
    from YL1203Code.memory.session import SessionManager
    consolidator, project = setup_consolidator(tmp_path, monkeypatch)
    consolidator._min_hours = 0
    consolidator._min_sessions = 1
    session = SessionManager(str(project)).create()
    session.close()
    target = Path(consolidator._mem_dir) / 'new.md'
    client = ScriptClient([ToolCallComplete('w', 'WriteFile', {'file_path': str(target), 'content': 'Consolidated'})])
    tasks = []
    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task
    monkeypatch.setattr(asyncio, 'ensure_future', schedule)
    await consolidator.maybe_run(client, None, 'anthropic')
    await asyncio.gather(*tasks)
    assert target.read_text() == 'Consolidated'