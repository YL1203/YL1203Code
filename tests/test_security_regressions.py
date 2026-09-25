from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from YL1203Code.agent import Agent
from YL1203Code.config import AppConfig, SandboxAppConfig, WorktreeConfig, _merge_config
from YL1203Code.hooks import Action, Hook, HookEngine
from YL1203Code.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, RuleEngine
from YL1203Code.permissions.dangerous import is_safe_command
from YL1203Code.remote import RemoteServer
from YL1203Code.tools import create_default_registry
from YL1203Code.tools.base import ToolCallComplete
from YL1203Code.tools.bash import Bash
from YL1203Code.tools.grep import Grep


def test_command_auto_approval_has_no_write_or_shell_escape() -> None:
    assert is_safe_command("git status")
    for command in (
        "sed -i s/a/b/ file.txt",
        "git branch -D old",
        "npx arbitrary-package",
        "echo ok & whoami",
        "git diff --output=file.txt",
    ):
        assert not is_safe_command(command)


def test_explicit_rule_precedes_safe_command(tmp_path) -> None:
    rules = Mock(spec=RuleEngine)
    rules.evaluate.return_value = "deny"
    checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(tmp_path)), rules)
    assert checker.check(Bash(), {"command": "git status"}).effect == "deny"


def test_search_checks_actual_path(tmp_path) -> None:
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(tmp_path)), RuleEngine()
    )
    assert checker.check(Grep(), {"pattern": "needle", "path": str(Path.cwd())}).effect == "ask"


def test_explicit_defaults_override_earlier_config() -> None:
    base = AppConfig(providers=[], permission_mode="bypassPermissions", enable_fork=True)
    override = AppConfig(
        providers=[], permission_mode="default", enable_fork=False,
        worktree=WorktreeConfig(symlink_directories=["custom"]),
        sandbox=SandboxAppConfig(enabled=False),
        _specified={
            "top": {"permission_mode", "enable_fork"},
            "worktree": {"symlink_directories"},
            "sandbox": {"enabled"},
        },
    )
    merged = _merge_config(base, override)
    assert merged.permission_mode == "default"
    assert merged.enable_fork is False
    assert merged.worktree.symlink_directories == ["custom"]


def test_remote_defaults_to_loopback_and_rejects_foreign_origin() -> None:
    server = RemoteServer(providers=[])
    assert server.addr == "127.0.0.1"
    request = Request("/ws", Headers({"Host": "localhost:18888", "Origin": "https://other.example"}))
    response = server._process_http_request(None, request)
    assert response is not None and response.status_code == 403


@pytest.mark.asyncio
async def test_main_agent_pre_tool_hook_can_reject(tmp_path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("secret", encoding="utf-8")
    hook = Hook(
        id="block-read", event="pre_tool_use",
        action=Action(type="prompt", message="blocked"), reject=True,
    )
    agent = Agent(None, create_default_registry(), "anthropic", work_dir=str(tmp_path), hook_engine=HookEngine([hook]))
    result = await agent._execute_single_tool_direct(
        ToolCallComplete("t1", "ReadFile", {"file_path": str(target)})
    )
    assert result.result.is_error
    assert "Hook rejected" in result.result.output
