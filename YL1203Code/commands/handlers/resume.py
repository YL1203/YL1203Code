from __future__ import annotations

from YL1203Code.commands.handlers.session import handle_session
from YL1203Code.commands.registry import Command, CommandContext, CommandType


async def handle_resume(ctx: CommandContext) -> None:
    manager = ctx.session_manager
    if manager is None:
        ctx.ui.add_system_message("会话管理器未初始化")
        return
    candidates = [
        meta for meta in manager.list()
        if meta.message_count > 0
        and (ctx.session is None or meta.id != ctx.session.session_id)
    ]
    session_id = ctx.args.strip()
    if not session_id:
        if not candidates:
            ctx.ui.add_system_message("当前项目没有可恢复的历史对话。")
            return
        session_id = await ctx.config["select_session"](candidates)
        if session_id is None:
            return
    if session_id not in {meta.id for meta in candidates}:
        ctx.ui.add_system_message("当前项目没有该历史对话，或它已是当前会话。")
        return
    ctx.args = "resume " + session_id
    await handle_session(ctx)


RESUME_COMMAND = Command(
    name="resume",
    description="选择当前项目的历史对话并继续",
    usage="/resume [会话ID]",
    type=CommandType.LOCAL_UI,
    handler=handle_resume,
)
