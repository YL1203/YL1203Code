from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from YL1203Code.filehistory import FileHistory
from YL1203Code.tools.base import ToolCallComplete
from test_skill_fork_execution import ScriptClient, make_agent


def test_change_preview_and_rewind_cover_new_and_modified_files(tmp_path):
    history = FileHistory(str(tmp_path), 'test-session')
    existing, created = tmp_path / 'existing.py', tmp_path / 'created.py'
    existing.write_text('before\n')
    history.make_snapshot(1, 'before changes')
    history.track_edit(str(existing))
    existing.write_text('after\n')
    history.track_edit(str(created))
    created.write_text('new\n')
    preview = history.diff()
    assert '-before' in preview and '+after' in preview
    assert '+new' in preview
    history.rewind(0)
    assert existing.read_text() == 'before\n'
    assert not created.exists()


@pytest.mark.asyncio
async def test_failed_tests_are_repaired_then_actually_rerun(tmp_path):
    from YL1203Code.testing import TestWorkflow
    script = tmp_path / 'check.py'
    script.write_text("from pathlib import Path\nimport sys\nsys.exit(0 if Path('fixed').exists() else 1)\n")
    args = [sys.executable, str(script)]
    command = subprocess.list2cmdline(args) if sys.platform == 'win32' else shlex.join(args)
    rules = tmp_path / 'permissions.yaml'
    rules.write_text('- rule: Bash(*)\n  effect: allow\n')
    client = ScriptClient([ToolCallComplete('w', 'WriteFile', {'file_path': 'fixed', 'content': 'yes'})])
    agent = make_agent(tmp_path, client, rules=rules)
    result = await TestWorkflow(agent).run(command, repair=True)
    assert result.passed
    assert result.attempts == 2
    assert (tmp_path / 'fixed').exists()


@pytest.mark.asyncio
async def test_failed_tests_stop_at_retry_limit(tmp_path):
    from YL1203Code.testing import TestWorkflow
    command = subprocess.list2cmdline([sys.executable, '-c', 'import sys; sys.exit(1)']) if sys.platform == 'win32' else shlex.join([sys.executable, '-c', 'import sys; sys.exit(1)'])
    rules = tmp_path / 'permissions.yaml'
    rules.write_text('- rule: Bash(*)\n  effect: allow\n')
    result = await TestWorkflow(make_agent(tmp_path, ScriptClient(), rules=rules)).run(command, repair=True, max_attempts=2)
    assert not result.passed
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_test_workflow_obeys_permission_denial(tmp_path):
    from YL1203Code.testing import TestWorkflow
    rules = tmp_path / 'permissions.yaml'
    rules.write_text('- rule: Bash(*)\n  effect: deny\n')
    result = await TestWorkflow(make_agent(tmp_path, ScriptClient(), rules=rules)).run('echo test', repair=True)
    assert not result.passed
    assert result.attempts == 1
    assert 'Permission denied' in result.output
