from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

import pytest

from YL1203Code.sandbox import Sandbox, SandboxConfig, create_sandbox
from YL1203Code.sandbox.bwrap import BwrapSandbox
from YL1203Code.tools.bash import Bash, Params


def test_missing_protected_path_protects_existing_parent(tmp_path):
    state = tmp_path / '.YL1203Code'
    state.mkdir()
    config = SandboxConfig(allow_write=[str(tmp_path)], deny_write=[str(state / 'config.local.yaml')])
    args = shlex.split(BwrapSandbox().wrap('echo safe', config))
    protected = [args[index + 1] for index, value in enumerate(args) if value == '--ro-bind']
    assert str(state.resolve()) in protected
    assert str(state / 'config.local.yaml') not in args


@pytest.mark.asyncio
async def test_disappearing_sandbox_never_runs_host_command(tmp_path):
    class Unavailable(Sandbox):
        def available(self):
            return False
        def wrap(self, command, config):
            raise AssertionError('must not wrap')
    bash = Bash()
    bash.work_dir = str(tmp_path)
    bash.sandbox = Unavailable()
    bash.sandbox_config = SandboxConfig()
    result = await bash.execute(Params(command='echo unsafe > escaped'))
    assert result.is_error
    assert not (tmp_path / 'escaped').exists()


def test_windows_selects_container_backend(monkeypatch):
    import YL1203Code.sandbox as module
    monkeypatch.setattr(module.platform, 'system', lambda: 'Windows')
    assert type(create_sandbox()).__name__ == 'DockerSandbox'


def test_docker_uses_explicit_argv_and_restricted_mounts(tmp_path):
    from YL1203Code.sandbox.docker import DockerSandbox
    state = tmp_path / '.YL1203Code'
    state.mkdir()
    backend = DockerSandbox(image='python:3.11-slim')
    command = 'echo "literal %PATH% $(value) & text"'
    args = backend.execution_args(command, SandboxConfig(
        allow_write=[str(tmp_path)], deny_write=[str(state / 'config.yaml')],
    ), str(tmp_path))
    assert args[-2:] == ['-c', command]
    assert '--read-only' in args
    assert args[args.index('--network') + 1] == 'none'
    assert args[args.index('--pull') + 1] == 'never'
    mounts = [args[index + 1] for index, item in enumerate(args) if item == '--mount']
    assert any('target=/workspace' in mount for mount in mounts)
    assert any('target=/workspace/.YL1203Code,readonly' in mount for mount in mounts)
    assert all('docker.sock' not in mount for mount in mounts)
    assert args[args.index('--entrypoint') + 1] == '/bin/bash'


def test_docker_missing_dependency_is_unavailable(monkeypatch):
    import YL1203Code.sandbox.docker as module
    monkeypatch.setattr(module.shutil, 'which', lambda name: None)
    assert not module.DockerSandbox().available()


@pytest.mark.asyncio
async def test_sandbox_command_cleanup_runs_when_cancelled(tmp_path, monkeypatch):
    class Backend(Sandbox):
        def available(self):
            return True
        def wrap(self, command, config):
            return command
        def execution_args(self, command, config, work_dir):
            return ['fake', command]
        async def cleanup(self, args):
            cleaned.append(args)
    cleaned = []
    waiting = asyncio.Event()
    class Process:
        async def communicate(self):
            waiting.set()
            await asyncio.Event().wait()
    async def spawn(*args, **kwargs):
        return Process()
    async def terminate(proc):
        pass
    import YL1203Code.tools.bash as module
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(module, 'terminate_process_tree', terminate)
    bash = Bash()
    bash.work_dir = str(tmp_path)
    bash.sandbox, bash.sandbox_config = Backend(), SandboxConfig()
    task = asyncio.create_task(bash.execute(Params(command='long test')))
    await asyncio.wait_for(waiting.wait(), .5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert cleaned == [['fake', 'long test']]


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_command_resource_cleanup(tmp_path, monkeypatch):
    class Backend(Sandbox):
        def available(self):
            return True
        def wrap(self, command, config):
            return command
        def execution_args(self, command, config, work_dir):
            return ['fake', command]
        async def cleanup(self, args):
            cleaned.append(True)
    started, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = []
    class Process:
        returncode = None
        async def communicate(self):
            started.set()
            await asyncio.Event().wait()
    async def spawn(*args, **kwargs):
        return Process()
    async def terminate(proc):
        stopping.set()
        await release.wait()
    import YL1203Code.tools.bash as module
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(module, 'terminate_process_tree', terminate)
    bash = Bash()
    bash.work_dir = str(tmp_path)
    bash.sandbox, bash.sandbox_config = Backend(), SandboxConfig()
    task = asyncio.create_task(bash.execute(Params(command='long')))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    await asyncio.wait_for(stopping.wait(), 1)
    task.cancel()
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert cleaned == [True]
    assert task.cancelled()
