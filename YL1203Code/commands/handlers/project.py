from __future__ import annotations

from YL1203Code.commands.registry import Command, CommandContext, CommandType
from YL1203Code.project import discover_project


async def handle_project(ctx: CommandContext) -> None:
    bash = ctx.agent.registry.get("Bash")
    platform = getattr(getattr(bash, "sandbox", None), "command_platform", None)
    ctx.ui.add_system_message(discover_project(ctx.agent.work_dir, command_platform=platform).summary())


PROJECT_COMMAND = Command("project", "查看项目结构和测试入口", CommandType.LOCAL,
                          handle_project, usage="/project")
