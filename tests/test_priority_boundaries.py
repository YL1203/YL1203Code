from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from YL1203Code.agents.task_manager import TaskManager
from YL1203Code.commands.registry import Command, CommandRegistry, CommandType
from YL1203Code.commands.handlers.skill_register import register_skill_commands
from YL1203Code.filehistory import FileHistory
from YL1203Code.permissions import PermissionMode
from YL1203Code.permissions.sandbox import PathSandbox
from YL1203Code.permissions.restored import RestoredChecker
from test_skill_fork_execution import ScriptClient, make_agent


def test_rewind_restores_files_first_edited_after_checkpoint(tmp_path):
    history = FileHistory(str(tmp_path), 'later-edits')
    history.make_snapshot(0, 'start')
    existing, created = tmp_path / 'existing', tmp_path / 'created'
    existing.write_text('original')
    history.track_edit(str(existing))
    existing.write_text('changed')
    history.track_edit(str(created))
    created.write_text('new')
    assert '+changed' in history.diff(0)
    history.rewind(0)
    assert existing.read_text() == 'original'
    assert not created.exists()
    assert history.diff() == ''


def test_child_policy_keeps_narrower_parent_root(tmp_path):
    root = tmp_path / 'narrow'
    root.mkdir()
    parent = make_agent(tmp_path, ScriptClient())
    parent.permission_checker.sandbox = PathSandbox(str(root), deny_write=['protected'])
    child = parent.permission_checker.for_child(PermissionMode.ACCEPT_EDITS, str(tmp_path))
    assert child.sandbox.project_root == root
    assert not child.sandbox.check(str(root / 'protected'))[0]


def test_restored_policy_copy_does_not_share_mutable_checkers(tmp_path):
    checker = make_agent(tmp_path, ScriptClient()).permission_checker
    restored = RestoredChecker(checker, checker.snapshot())
    clone = restored.copy()
    clone.current.mode = PermissionMode.BYPASS
    assert restored.current.mode == PermissionMode.ACCEPT_EDITS
    clone.saved.add_session_allow('Bash', '*')
    assert not restored.saved._session_allowed


@pytest.mark.asyncio
async def test_immediate_task_cancel_is_checkpointed_and_notified(tmp_path):
    manager = TaskManager(str(tmp_path))
    task_id = manager.launch(make_agent(tmp_path, ScriptClient()), 'cancel before starting')
    task = manager._async_tasks[task_id]
    assert manager.cancel(task_id)
    await asyncio.gather(task, return_exceptions=True)
    assert manager.get(task_id).status == 'cancelled'
    assert manager.poll_completed()[0].id == task_id
    assert manager.poll_completed() == []
    assert TaskManager(str(tmp_path)).saved_tasks()[0]['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_resume_checkpoint_write_failure_keeps_original_recoverable(tmp_path, monkeypatch):
    manager = TaskManager(str(tmp_path))
    old_id = manager.launch(make_agent(tmp_path, ScriptClient()), 'saved')
    await manager._async_tasks[old_id]
    manager._saved[old_id]['status'] = 'interrupted'
    manager._store.save(manager._saved[old_id])
    restarted = TaskManager(str(tmp_path))
    def fail(data):
        raise OSError('disk full')
    monkeypatch.setattr(restarted._store, 'save', fail)
    with pytest.raises(OSError, match='disk full'):
        restarted.resume(old_id, make_agent(tmp_path, ScriptClient()))
    assert restarted._saved[old_id]['status'] == 'interrupted'
    assert restarted.list_tasks() == []
    assert not restarted._async_tasks
    assert TaskManager(str(tmp_path)).saved_tasks()[0]['status'] == 'interrupted'


def test_skill_refresh_is_scoped_to_command_registry():
    class Loader:
        def __init__(self, names):
            self.names = names
        def get_catalog(self):
            return [(name, name) for name in self.names]
    async def handler(ctx):
        pass
    first, second = CommandRegistry(), CommandRegistry()
    register_skill_commands(first, Loader(['one']))
    second.register_sync(Command('one', 'built in', CommandType.LOCAL, handler))
    register_skill_commands(second, Loader(['two']))
    assert second.find('one') is not None
    register_skill_commands(first, Loader([]))
    assert first.find('one') is None
    assert second.find('two') is not None


def test_node_tests_directory_does_not_select_pytest(tmp_path):
    from YL1203Code.project import discover_project
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'package.json').write_text('{"scripts":{"test":"vitest"}}')
    (tmp_path / 'tests' / 'example.ts').write_text('')
    assert discover_project(str(tmp_path)).test_commands == ['npm test']


def test_project_scan_limits_individual_entries(tmp_path):
    from YL1203Code.project import discover_project
    for index in range(2100):
        (tmp_path / f'{index}.py').touch()
    info = discover_project(str(tmp_path))
    assert info.languages['Python'] <= 2000


@pytest.mark.asyncio
async def test_test_workflow_honors_time_budget(tmp_path):
    from YL1203Code.testing import TestWorkflow
    from YL1203Code.tools.bash import Bash
    from YL1203Code.tools.base import ToolResult
    agent = make_agent(tmp_path, ScriptClient(), mode=PermissionMode.BYPASS)
    agent.timeout_seconds = .02
    class SlowBash(Bash):
        async def execute(self, params):
            await asyncio.sleep(.2)
            return ToolResult('ok')
    agent.registry.register(SlowBash())
    result = await TestWorkflow(agent).run('slow test')
    assert not result.passed
    assert 'timeout' in result.output.lower()


@pytest.mark.asyncio
async def test_test_workflow_routes_permission_request(tmp_path):
    from YL1203Code.agent import PermissionResponse
    from YL1203Code.testing import TestWorkflow
    from YL1203Code.tools.bash import Bash
    from YL1203Code.tools.base import ToolResult
    agent = make_agent(tmp_path, ScriptClient(), mode=PermissionMode.DEFAULT)
    class FakeBash(Bash):
        async def execute(self, params):
            return ToolResult('ok')
    agent.registry.register(FakeBash())
    requests = []
    async def consent(request):
        requests.append(request.tool_name)
        request.future.set_result(PermissionResponse.ALLOW)
    result = await TestWorkflow(agent, permission_handler=consent).run('pytest')
    assert result.passed
    assert requests == ['Bash']


@pytest.mark.asyncio
async def test_cli_waits_for_ordinary_background_tasks_before_final_result(tmp_path, monkeypatch, capsys):
    import json
    import YL1203Code.client as clients
    import YL1203Code.runtime as runtimes
    from YL1203Code.__main__ import _run_prompt
    from YL1203Code.config import AppConfig
    from YL1203Code.tools.base import TextDelta, StreamEnd
    from test_runtime_initialization import provider
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    class FinishClient(ScriptClient):
        async def stream(self, conv, **kwargs):
            self.seen.append(str(conv.history))
            yield TextDelta('main-result')
            yield StreamEnd('end_turn')
    class SlowClient:
        async def stream(self, conv, **kwargs):
            await asyncio.sleep(.02)
            yield TextDelta('background-result')
            yield StreamEnd('end_turn')
    client = FinishClient()
    monkeypatch.setattr(clients, 'create_client', lambda p: client)
    async def no_fetch(p):
        pass
    monkeypatch.setattr(clients, 'resolve_context_window', no_fetch)
    create = runtimes.create_runtime
    def with_background(*args, **kwargs):
        runtime = create(*args, **kwargs)
        runtime.task_manager.launch(make_agent(tmp_path, SlowClient()), 'background')
        return runtime
    monkeypatch.setattr(runtimes, 'create_runtime', with_background)
    assert not await _run_prompt(AppConfig([provider()]), PermissionMode.ACCEPT_EDITS, None, 'go', 'stream-json')
    assert any('background-result' in seen for seen in client.seen)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['type'] == 'result'
    assert sum(event['type'] == 'result' for event in events) == 1


@pytest.mark.asyncio
async def test_cli_mcp_init_failure_closes_runtime(tmp_path, monkeypatch, capsys):
    import YL1203Code.client as clients
    import YL1203Code.runtime as runtimes
    import YL1203Code.mcp as mcp
    from YL1203Code.__main__ import _run_prompt
    from YL1203Code.config import AppConfig, MCPServerConfig
    from test_runtime_initialization import provider
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(clients, 'create_client', lambda p: ScriptClient())
    async def no_fetch(p):
        pass
    monkeypatch.setattr(clients, 'resolve_context_window', no_fetch)
    closed = []
    create = runtimes.create_runtime
    def capture(*args, **kwargs):
        runtime = create(*args, **kwargs)
        close = runtime.close
        async def tracked():
            closed.append(True)
            await close()
        runtime.close = tracked
        return runtime
    monkeypatch.setattr(runtimes, 'create_runtime', capture)
    shutdown = []
    class BrokenMCP:
        def load_configs(self, configs):
            pass
        async def register_all_tools(self, registry):
            raise RuntimeError('init failed')
        async def shutdown(self):
            shutdown.append(True)
    monkeypatch.setattr(mcp, 'MCPManager', BrokenMCP)
    config = AppConfig([provider()], mcp_servers=[MCPServerConfig('broken', command='none')])
    try:
        failed = await _run_prompt(config, PermissionMode.DEFAULT, None, 'go')
    except RuntimeError:
        failed = True
    assert failed
    assert closed == [True]
    assert shutdown == [True]


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows process query')
def test_windows_memory_lock_never_sends_control_signal(monkeypatch):
    import os
    from YL1203Code.memory.consolidation import _is_process_running
    def forbidden(*args):
        raise AssertionError('process query must not send signals')
    monkeypatch.setattr(os, 'kill', forbidden)
    assert _is_process_running(os.getpid())
    assert not _is_process_running(999999999)


def test_checkpoint_restores_its_current_state_instead_of_previous_edit(tmp_path):
    history = FileHistory(str(tmp_path), 'current-state')
    file = tmp_path / 'code'
    file.write_text('original')
    history.track_edit(str(file))
    file.write_text('first change')
    history.make_snapshot(1, 'After first change')
    history.track_edit(str(file))
    file.write_text('second change')
    history.rewind(0)
    assert file.read_text() == 'first change'


def test_container_project_commands_use_linux_environment(tmp_path):
    from YL1203Code.project import discover_project
    (tmp_path / 'pyproject.toml').touch()
    (tmp_path / 'pom.xml').touch()
    (tmp_path / 'mvnw').touch()
    info = discover_project(str(tmp_path), command_platform='linux')
    assert info.test_commands == ['python -m pytest', './mvnw test']


@pytest.mark.asyncio
async def test_tui_ctrl_c_keeps_busy_until_local_command_cleanup_finishes():
    from types import SimpleNamespace
    from YL1203Code.app import YL1203CodeApp
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    app = SimpleNamespace(_streaming=True, _agent_task=None)
    app._show_system_message = lambda message: None
    app._show_error = lambda message: None
    app.query_one = lambda *args: None
    def finish():
        app._streaming = False
        app._agent_task = None
    app._finish_streaming = finish
    async def handler(ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
    task = asyncio.create_task(YL1203CodeApp._run_local_command(app, SimpleNamespace(handler=handler), None))
    app._agent_task = task
    await started.wait()
    ctrl_c = asyncio.create_task(YL1203CodeApp.action_handle_ctrl_c(app))
    await cleaning.wait()
    try:
        assert app._streaming
        assert app._agent_task is task
    finally:
        release.set()
        await asyncio.gather(task, ctrl_c, return_exceptions=True)
    assert not app._streaming


@pytest.mark.asyncio
async def test_noninteractive_explicit_ask_is_not_bypassed(tmp_path):
    from YL1203Code.tools.base import ToolCallComplete
    rules = tmp_path / 'permissions.yaml'
    rules.write_text('- rule: WriteFile(*)\n  effect: ask\n')
    agent = make_agent(tmp_path, ScriptClient(), mode=PermissionMode.BYPASS, rules=rules)
    result = await agent._execute_tool_noninteractive(
        ToolCallComplete('w', 'WriteFile', {'file_path': 'approval-required', 'content': 'no'}))
    assert result.is_error
    assert not (tmp_path / 'approval-required').exists()


@pytest.mark.asyncio
async def test_remote_prompt_command_can_trigger_followup(tmp_path, monkeypatch):
    import YL1203Code.remote as module
    from test_runtime_initialization import provider
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(module, 'create_client', lambda p: ScriptClient())
    server = module.RemoteServer([provider()])
    server._init_agent()
    received = asyncio.Event()
    prompts = []
    async def handle(text):
        prompts.append(text)
        received.set()
    server._handle_user_message = handle
    async def command():
        server.send_user_message('review project')
    server._active_task = asyncio.create_task(command())
    try:
        await server._active_task
        await asyncio.wait_for(received.wait(), .1)
        assert prompts == ['review project']
    finally:
        await server._runtime.close()
