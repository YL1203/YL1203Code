from __future__ import annotations

from YL1203Code.commands.handlers.clear import CLEAR_COMMAND
from YL1203Code.commands.handlers.compact import COMPACT_COMMAND
from YL1203Code.commands.handlers.help import HELP_COMMAND
from YL1203Code.commands.handlers.mcp import MCP_COMMAND
from YL1203Code.commands.handlers.memory import MEMORY_COMMAND
from YL1203Code.commands.handlers.permission import PERMISSION_COMMAND
from YL1203Code.commands.handlers.plan import PLAN_COMMAND
from YL1203Code.commands.handlers.sandbox import SANDBOX_COMMAND
from YL1203Code.commands.handlers.session import SESSION_COMMAND
from YL1203Code.commands.handlers.resume import RESUME_COMMAND
from YL1203Code.commands.handlers.skill import SKILL_COMMAND
from YL1203Code.commands.handlers.rewind import REWIND_COMMAND
from YL1203Code.commands.handlers.review import REVIEW_COMMAND
from YL1203Code.commands.handlers.project import PROJECT_COMMAND
from YL1203Code.commands.handlers.diff import DIFF_COMMAND
from YL1203Code.commands.handlers.test import TEST_COMMAND
from YL1203Code.commands.handlers.status import STATUS_COMMAND
from YL1203Code.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    RESUME_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    PERMISSION_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    REVIEW_COMMAND,
    PROJECT_COMMAND,
    DIFF_COMMAND,
    TEST_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

