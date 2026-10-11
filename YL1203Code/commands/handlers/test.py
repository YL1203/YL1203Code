from __future__ import annotations

from YL1203Code.commands.registry import Command, CommandContext, CommandType
from YL1203Code.project import discover_project
from YL1203Code.testing import TestWorkflow


async def handle_test(ctx: CommandContext) -> None:
    args = ctx.args.strip()
    repair = args == "--fix" or args.startswith("--fix ")
    command = args[5:].strip() if repair else args
    if not command:
        bash = ctx.agent.registry.get("Bash")
        platform = getattr(getattr(bash, "sandbox", None), "command_platform", None)
        candidates = discover_project(ctx.agent.work_dir, command_platform=platform).test_commands
        if not candidates:
            ctx.ui.add_system_message("未识别测试入口，请使用 /test [--fix] <命令>。")
            return
        command = candidates[0]
    ctx.ui.add_system_message(f"运行测试: {command}")
    history = getattr(ctx.agent, "file_history", None)
    if history:
        history.make_snapshot(len(ctx.conversation.history), "/test " + args)
    result = await TestWorkflow(
        ctx.agent, permission_handler=ctx.config.get("permission_handler"),
    ).run(command, repair=repair)
    status = "通过" if result.passed else "未通过"
    ctx.ui.add_system_message(f"测试{status}，共执行 {result.attempts} 次。\n{result.output}")


TEST_COMMAND = Command("test", "运行测试，可选择失败后修复并重跑", CommandType.LOCAL,
                       handle_test, usage="/test [--fix] [command]")
