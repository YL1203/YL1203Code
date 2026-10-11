from __future__ import annotations

from pathlib import Path

import pytest

from YL1203Code.config import AppConfig, ProviderConfig, SandboxAppConfig
from YL1203Code.permissions import PermissionMode
from test_skill_fork_execution import ScriptClient


def provider():
    return ProviderConfig('test', 'anthropic', 'https://example.invalid', 'test-model', api_key='test-only')


@pytest.mark.asyncio
async def test_common_runtime_initializes_complete_core(tmp_path, monkeypatch):
    from YL1203Code.runtime import create_runtime
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    runtime = create_runtime(AppConfig([provider()]), PermissionMode.ACCEPT_EDITS,
                             str(tmp_path), client=ScriptClient())
    try:
        names = {tool.name for tool in runtime.registry.list_tools()}
        assert {'ReadFile', 'Bash', 'LoadSkill', 'Agent', 'TeamCreate', 'TeamDelete',
                'EnterWorktree', 'ExitWorktree', 'ToolSearch'} <= names
        assert runtime.agent.memory_manager is runtime.memory_manager
        assert runtime.agent.file_history is runtime.file_history
        assert runtime.agent.session_id == runtime.session.session_id
        assert runtime.agent.max_iterations > 0
        assert Path(runtime.registry.get('Bash').work_dir) == tmp_path
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_remote_has_same_core_tools(tmp_path, monkeypatch):
    import YL1203Code.remote as remote
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(remote, 'create_client', lambda p: ScriptClient())
    server = remote.RemoteServer([provider()], permission_mode=PermissionMode.ACCEPT_EDITS)
    server._init_agent()
    try:
        assert server.registry.is_enabled('Agent')
        assert server.registry.is_enabled('EnterWorktree')
        assert server.command_registry.find('worktree') is not None
        assert server.command_registry.find('tasks') is not None
        assert server.agent.file_history is not None
    finally:
        if getattr(server, '_runtime', None):
            await server._runtime.close()
        elif server.session:
            server.session.close()


@pytest.mark.asyncio
async def test_unavailable_os_sandbox_never_auto_allows(tmp_path, monkeypatch):
    import YL1203Code.runtime as runtime_module
    monkeypatch.setattr(runtime_module, 'create_sandbox', lambda: None)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    config = AppConfig([provider()], sandbox=SandboxAppConfig(enabled=True, auto_allow=True))
    runtime = runtime_module.create_runtime(config, PermissionMode.DEFAULT, str(tmp_path), client=ScriptClient())
    try:
        assert not runtime.agent.permission_checker.sandbox_enabled
        assert runtime.registry.get('Bash').sandbox is None
        assert runtime.warnings
    finally:
        await runtime.close()

@pytest.mark.asyncio
async def test_tui_initializes_common_runtime(tmp_path, monkeypatch):
    import YL1203Code.app as app_module
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(app_module, 'create_client', lambda p: ScriptClient())
    async def no_fetch(provider):
        return None
    monkeypatch.setattr(app_module, 'resolve_context_window', no_fetch)
    app = app_module.YL1203CodeApp([provider()])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.agent is not None
        assert app.agent.session_id == app.session.session_id
        assert app.registry.is_enabled('Agent')
        assert app.registry.is_enabled('EnterWorktree')
        assert app.command_registry.find('tasks') is not None
        app._stale_cleanup_task.cancel()
        app._notification_check_task.cancel()
        await app._runtime.close()


@pytest.mark.asyncio
async def test_cli_reports_budget_failure_and_saves_session(tmp_path, monkeypatch, capsys):
    import json
    import YL1203Code.client as clients
    from YL1203Code.__main__ import _run_prompt
    from YL1203Code.config import ExecutionConfig
    from test_execution_budgets import LoopingClient
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(clients, 'create_client', lambda p: LoopingClient())
    async def no_fetch(provider):
        return None
    monkeypatch.setattr(clients, 'resolve_context_window', no_fetch)
    config = AppConfig([provider()], execution=ExecutionConfig(max_iterations=1))
    failed = await _run_prompt(config, PermissionMode.ACCEPT_EDITS, None, 'Keep reading', 'stream-json')
    assert failed
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]['type'] == 'result'
    assert records[-1]['stop_reason'] == 'iteration_limit'
    assert not records[-1]['success']
    assert list((tmp_path / '.YL1203Code' / 'sessions').glob('*.jsonl'))

@pytest.mark.asyncio
async def test_subagent_inherits_limits_rules_and_tool_ownership(tmp_path, monkeypatch):
    from YL1203Code.runtime import create_runtime
    from YL1203Code.config import ExecutionConfig
    from YL1203Code.tools.agent_tool import AgentToolParams
    from YL1203Code.tools.base import ToolCallComplete
    from YL1203Code.skills.parser import SkillDef
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    config_dir = tmp_path / '.YL1203Code'
    config_dir.mkdir()
    (config_dir / 'permissions.yaml').write_text('- rule: WriteFile(*)\n  effect: deny\n')
    client = ScriptClient([
        ToolCallComplete('s', 'LoadSkill', {'name': 'nested'}),
        ToolCallComplete('w', 'WriteFile', {'file_path': 'forbidden.txt', 'content': 'bad'}),
    ])
    runtime = create_runtime(AppConfig([provider()], execution=ExecutionConfig(3, 123, 4)),
                             PermissionMode.ACCEPT_EDITS, str(tmp_path), client=client)
    class Loader:
        def get(self, name):
            return SkillDef(name, 'nested', 'Nested instructions')
    runtime.registry.get('LoadSkill').set_loader(Loader())
    try:
        result = await runtime.registry.get('Agent').execute(AgentToolParams(
            prompt='Check code', description='inspect', subagent_type='general-purpose', run_in_background=True))
        assert not result.is_error
        child = runtime.task_manager.list_tasks()[-1].agent
        assert child.max_total_tokens == 123
        assert child.timeout_seconds == 4
        task = next(iter(runtime.task_manager._async_tasks.values()))
        await task
        assert not (tmp_path / 'forbidden.txt').exists()
        assert not runtime.agent.active_skills
        assert 'nested' in child.active_skills
    finally:
        await runtime.close()

@pytest.mark.asyncio
async def test_remote_delivers_completed_background_result(tmp_path, monkeypatch):
    import YL1203Code.remote as remote
    from YL1203Code.tools.agent_tool import AgentToolParams
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(remote, 'create_client', lambda p: ScriptClient())
    server = remote.RemoteServer([provider()])
    server._init_agent()
    runtime = server._runtime
    try:
        await server.registry.get('Agent').execute(AgentToolParams(
            prompt='inspect', description='inspection', subagent_type='general-purpose', run_in_background=True))
        task = next(iter(runtime.task_manager._async_tasks.values()))
        await task
        notifications = server.agent.notification_fn()
        assert any('task-notification' in notification for notification in notifications)
        assert runtime.task_manager._notify_queue.empty()
    finally:
        await runtime.close()