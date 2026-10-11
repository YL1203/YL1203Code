from __future__ import annotations

import asyncio
import copy
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from YL1203Code.config import AppConfig
from YL1203Code.permissions import PermissionMode
from YL1203Code.runtime import create_runtime
from YL1203Code.testing import TestWorkflow
from YL1203Code.tools.base import StreamEnd, ToolCallComplete
from test_runtime_initialization import provider
from test_skill_fork_execution import ScriptClient


def git(root, *args):
    result = subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}',
                             '-c', 'user.name=Integration Test', '-c', 'user.email=test@example.invalid',
                             *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize('rollback', [False, True])
async def test_real_repository_worktree_fix_test_diff_and_rewind(tmp_path, monkeypatch, rollback):
    root = tmp_path / 'repo'
    root.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    (root / '.gitignore').write_text('.YL1203Code/\n__pycache__/\n')
    original = 'def add(a, b):\n    return a - b\n'
    (root / 'calc.py').write_text(original)
    (root / 'check.py').write_text('from calc import add\nassert add(2, 3) == 5\n')
    git(root, 'init')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Initial integration fixture')
    client = ScriptClient([
        ToolCallComplete('read', 'ReadFile', {'file_path': 'calc.py'}),
        ToolCallComplete('fix', 'EditFile', {'file_path': 'calc.py', 'old_string': 'a - b', 'new_string': 'a + b'}),
        ToolCallComplete('note', 'WriteFile', {'file_path': 'fix-note.txt', 'content': 'Fixed addition'}),
    ])
    runtime = create_runtime(AppConfig([provider()]), PermissionMode.BYPASS, str(root), client=client)
    agent = runtime.agent
    process_cwd = os.getcwd()
    try:
        entered = await agent._execute_tool_noninteractive(ToolCallComplete('enter', 'EnterWorktree', {'name': 'integration'}))
        assert not entered.is_error, entered.output
        worktree = Path(agent.work_dir)
        assert worktree != root
        runtime.file_history.make_snapshot(0, 'Before repair')
        argv = [sys.executable, '-B', 'check.py']
        command = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
        result = await TestWorkflow(agent).run(command, repair=True)
        assert result.passed, result.output
        assert result.attempts == 2
        assert 'a + b' in (worktree / 'calc.py').read_text()
        assert (root / 'calc.py').read_text() == original
        preview = runtime.file_history.diff(0)
        assert '-    return a - b' in preview and '+    return a + b' in preview
        assert '+Fixed addition' in preview
        if rollback:
            runtime.file_history.rewind(0)
            assert (worktree / 'calc.py').read_text() == original
            assert not (worktree / 'fix-note.txt').exists()
            assert git(worktree, 'diff', '--', 'calc.py') == ''
        else:
            assert 'a + b' in git(worktree, 'diff', '--', 'calc.py')
        exited = await agent._execute_tool_noninteractive(ToolCallComplete('exit', 'ExitWorktree', {'action': 'keep'}))
        assert not exited.is_error, exited.output
        assert Path(agent.work_dir) == root
        assert worktree.exists()
        assert os.getcwd() == process_cwd
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restart_in_real_worktree_resumes_checkpoint_without_repeating_write(tmp_path, monkeypatch):
    from YL1203Code.agent import Agent
    from YL1203Code.agents.task_manager import TaskManager
    root = tmp_path / 'repo'
    root.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    (root / '.gitignore').write_text('.YL1203Code/\n')
    git(root, 'init')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Initial integration fixture')
    reached = asyncio.Event()
    class Interrupted:
        def __init__(self):
            self.calls = 0
        async def stream(self, conv, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ToolCallComplete('w', 'WriteFile', {'file_path': 'once.txt', 'content': 'Only once'})
                yield StreamEnd('end_turn')
            else:
                reached.set()
                await asyncio.Event().wait()
    runtime = create_runtime(AppConfig([provider()]), PermissionMode.BYPASS, str(root), client=ScriptClient())
    restarted_runtime = None
    try:
        assert not (await runtime.agent._execute_tool_noninteractive(
            ToolCallComplete('enter', 'EnterWorktree', {'name': 'resume'}))).is_error
        worktree = Path(runtime.agent.work_dir)
        child = Agent(Interrupted(), runtime.registry.copy(), 'anthropic', work_dir=str(worktree),
                      permission_checker=runtime.agent.permission_checker.copy(), max_iterations=5)
        task_id = runtime.task_manager.launch(child, 'Write then inspect')
        await asyncio.wait_for(reached.wait(), 5)
        before = (worktree / 'once.txt').stat().st_mtime_ns
        restarted_runtime = create_runtime(AppConfig([provider()]), PermissionMode.BYPASS, str(root), client=ScriptClient())
        assert Path(restarted_runtime.agent.work_dir) == worktree
        record = next(r for r in restarted_runtime.task_manager.saved_tasks() if r['id'] == task_id)
        assert record['status'] == 'interrupted'
        assert restarted_runtime.task_manager.list_tasks() == []
        pending = runtime.task_manager._async_tasks[task_id]
        runtime.task_manager.cancel(task_id)
        await pending
        resumed = restarted_runtime.task_manager.resume(task_id, restarted_runtime.agent)
        await restarted_runtime.task_manager._async_tasks[resumed]
        assert restarted_runtime.task_manager.get(resumed).status == 'completed'
        assert (worktree / 'once.txt').stat().st_mtime_ns == before
        assert (worktree / 'once.txt').read_text() == 'Only once'
        assert not (root / 'once.txt').exists()
    finally:
        await runtime.close()
        if restarted_runtime:
            await restarted_runtime.close()
