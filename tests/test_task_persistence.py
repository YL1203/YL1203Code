from __future__ import annotations

import asyncio
import json

import pytest

from YL1203Code.agents.task_manager import TaskManager
from YL1203Code.tools.base import StreamEnd, TextDelta, ToolCallComplete
from test_skill_fork_execution import ScriptClient, make_agent


@pytest.mark.asyncio
async def test_completed_task_is_persisted(tmp_path):
    manager = TaskManager(work_dir=str(tmp_path))
    agent = make_agent(tmp_path, ScriptClient())
    task_id = manager.launch(agent, 'Inspect code', 'inspection')
    await manager._async_tasks[task_id]
    restarted = TaskManager(work_dir=str(tmp_path))
    records = restarted.saved_tasks()
    assert len(records) == 1
    assert records[0]['id'] == task_id
    assert records[0]['status'] == 'completed'
    assert records[0]['task'] == 'Inspect code'
    assert records[0]['history']
    assert restarted.list_tasks() == []


@pytest.mark.asyncio
async def test_interrupted_task_resumes_without_replaying_write(tmp_path):
    changed = tmp_path / 'changed.txt'
    progressed = asyncio.Event()
    class InterruptedClient:
        def __init__(self):
            self.calls = 0
        async def stream(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ToolCallComplete('w', 'WriteFile', {'file_path': 'changed.txt', 'content': 'already written'})
                yield StreamEnd('end_turn')
            else:
                progressed.set()
                await asyncio.Event().wait()
    manager = TaskManager(work_dir=str(tmp_path))
    task_id = manager.launch(make_agent(tmp_path, InterruptedClient()), 'Fix code')
    await asyncio.wait_for(progressed.wait(), 3)
    assert changed.read_text() == 'already written'
    # Reopening the task store models a new process; it must not resume automatically.
    restarted = TaskManager(work_dir=str(tmp_path))
    assert restarted.saved_tasks()[0]['status'] == 'interrupted'
    assert restarted.list_tasks() == []
    manager.cancel(task_id)
    await manager._async_tasks[task_id]
    mtime = changed.stat().st_mtime_ns
    client = ScriptClient()
    recovered_id = restarted.resume(task_id, make_agent(tmp_path, client))
    await restarted._async_tasks[recovered_id]
    assert changed.stat().st_mtime_ns == mtime
    text = '\n'.join(r.content for m in client.seen[0] for r in m.tool_results)
    assert 'Successfully wrote' in text
    assert restarted.get(recovered_id).status == 'completed'


def test_bad_checkpoint_does_not_break_startup(tmp_path):
    directory = tmp_path / '.YL1203Code' / 'tasks'
    directory.mkdir(parents=True)
    (directory / 'bad.json').write_text('{broken')
    manager = TaskManager(work_dir=str(tmp_path))
    assert manager.saved_tasks() == []

@pytest.mark.asyncio
async def test_resume_preserves_original_tool_restrictions(tmp_path):
    manager = TaskManager(work_dir=str(tmp_path))
    original = make_agent(tmp_path, ScriptClient())
    original.registry.disable('WriteFile')
    task_id = manager.launch(original, 'Read only')
    await manager._async_tasks[task_id]
    record = manager._saved[task_id]
    record['status'] = 'interrupted'
    manager._store.save(record)
    restarted = TaskManager(work_dir=str(tmp_path))
    client = ScriptClient([ToolCallComplete('w', 'WriteFile', {'file_path': 'forbidden.txt', 'content': 'bad'})])
    resumed = restarted.resume(task_id, make_agent(tmp_path, client))
    await restarted._async_tasks[resumed]
    assert not (tmp_path / 'forbidden.txt').exists()


@pytest.mark.asyncio
async def test_resume_immediately_persists_original_history(tmp_path):
    manager = TaskManager(work_dir=str(tmp_path))
    original = make_agent(tmp_path, ScriptClient([ToolCallComplete('w', 'WriteFile', {'file_path': 'done.txt', 'content': 'done'})]))
    task_id = manager.launch(original, 'Write once')
    await manager._async_tasks[task_id]
    record = manager._saved[task_id]
    record['status'] = 'interrupted'
    manager._store.save(record)
    restarted = TaskManager(work_dir=str(tmp_path))
    resumed = restarted.resume(task_id, make_agent(tmp_path, ScriptClient()))
    try:
        reloaded = TaskManager(work_dir=str(tmp_path))
        new_record = next(r for r in reloaded.saved_tasks() if r['id'] == resumed)
        assert any(m.get('tool_results') for m in new_record['history'])
    finally:
        restarted.cancel(resumed)
        await asyncio.gather(restarted._async_tasks[resumed], return_exceptions=True)