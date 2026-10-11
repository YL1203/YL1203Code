from __future__ import annotations

from YL1203Code.commands.registry import Command, CommandContext, CommandType


async def handle_diff(ctx: CommandContext) -> None:
    history = getattr(ctx.agent, "file_history", None)
    if history is None:
        ctx.ui.add_system_message("暂无文件变更记录。")
        return
    try:
        index = int(ctx.args.strip()) - 1 if ctx.args.strip() else None
        if index is not None and index < 0:
            raise ValueError("Checkpoint numbers start at 1")
        preview = history.diff(index)
    except (ValueError, IndexError) as exc:
        ctx.ui.add_system_message(f"检查点无效: {exc}")
        return
    ctx.ui.add_system_message(preview or "当前会话暂无文件变更。")


DIFF_COMMAND = Command("diff", "预览当前会话的文件变更", CommandType.LOCAL,
                       handle_diff, usage="/diff [checkpoint]")
