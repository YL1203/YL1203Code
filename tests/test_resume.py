from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input

from YL1203Code.app import YL1203CodeApp
from YL1203Code.commands.handlers import register_all_commands
from YL1203Code.commands.registry import CommandContext, CommandRegistry
from YL1203Code.conversation import ConversationManager, Message
from YL1203Code.memory.session import SessionManager
from YL1203Code.session_dialog import InlineResumeWidget


@pytest.mark.asyncio
async def test_resume_registry_picker_and_continue(tmp_path):
    registry = CommandRegistry()
    register_all_commands(registry)
    command = registry.find("resume")
    assert command is not None
    sm = SessionManager(str(tmp_path))
    saved = sm.create()
    saved.append(Message(role="user", content="remember 1203"))
    saved.append(Message(role="assistant", content="remembered"))
    saved.close()
    current = sm.create()
    state = {}
    picker = AsyncMock(return_value=saved.session_id)
    ctx = CommandContext("", None, ConversationManager(), current, sm, None, Mock(), {
        "select_session": picker,
        "set_session": lambda value: state.update(session=value),
        "set_conversation": lambda value: state.update(conversation=value),
        "render_restored": AsyncMock(),
    })
    await command.handler(ctx)
    assert [m.id for m in picker.call_args.args[0]] == [saved.session_id]
    assert current._file.closed
    assert state["conversation"].history[0].content == "remember 1203"
    resumed = state["session"]
    resumed.append(Message(role="user", content="continue"))
    resumed.close()
    loaded = sm.resume(saved.session_id)
    assert loaded.messages[-1].content == "continue"
    loaded.session.close()


@pytest.mark.asyncio
async def test_picker_can_reach_sessions_beyond_first_page():
    smetas = [SimpleNamespace(id=str(i), title=f"title {i}", message_count=2,
              last_active=datetime.now(timezone.utc))
              for i in range(15)]

    class PickerApp(App):
        def compose(self) -> ComposeResult:
            yield InlineResumeWidget(smetas)

    app = PickerApp()
    async with app.run_test() as pilot:
        widget = app.query_one(InlineResumeWidget)
        for _ in range(12):
            widget.action_cursor_down()
        assert widget._cursor == 12
        assert "title 12" in widget._build_content()

class ResumeApp(App):
    _select_session = YL1203CodeApp._select_session
    on_inline_resume_widget_selected = YL1203CodeApp.on_inline_resume_widget_selected

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-area")
        yield Input(id="chat-input")


@pytest.mark.asyncio
async def test_tui_select_cancel_and_reopen(tmp_path):
    sm = SessionManager(str(tmp_path))
    session = sm.create()
    session.append(Message(role="user", content="[red]标题[/red]"))
    session.close()
    app = ResumeApp()
    async with app.run_test(size=(100, 35)) as pilot:
        for key, expected in [("enter", session.session_id), ("escape", None), ("enter", session.session_id)]:
            task = asyncio.create_task(app._select_session(sm.list()))
            await pilot.pause()
            assert app.query_one("#chat-input").disabled
            await pilot.press(key)
            assert await asyncio.wait_for(task, 2) == expected
            assert not list(app.query(InlineResumeWidget))
            assert not app.query_one("#chat-input").disabled
        task = asyncio.create_task(app._select_session(sm.list()))
        await pilot.pause()
        await pilot.press("z", "z")
        assert app.query_one(InlineResumeWidget)._filtered == []
        await pilot.press("backspace", "backspace")
        assert len(app.query_one(InlineResumeWidget)._filtered) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list(app.query(InlineResumeWidget))
        assert not app.query_one("#chat-input").disabled


@pytest.mark.asyncio
async def test_empty_cancel_and_foreign_project_leave_current_untouched(tmp_path):
    registry = CommandRegistry()
    register_all_commands(registry)
    command = registry.find("resume")
    sm = SessionManager(str(tmp_path / "one"))
    other = SessionManager(str(tmp_path / "two"))
    foreign = other.create()
    foreign.append(Message(role="user", content="foreign"))
    foreign.close()
    current = sm.create()
    setter = Mock()
    picker = AsyncMock(return_value=None)
    ctx = CommandContext("", None, ConversationManager(), current, sm, None, Mock(), {
        "select_session": picker, "set_session": setter,
    })
    await command.handler(ctx)
    picker.assert_not_called()
    ctx.args = foreign.session_id
    await command.handler(ctx)
    setter.assert_not_called()
    assert not current._file.closed
    saved = sm.create()
    saved.append(Message(role="user", content="local"))
    saved.close()
    ctx.args = ""
    await command.handler(ctx)
    picker.assert_awaited_once()
    setter.assert_not_called()
    assert not current._file.closed
    current.close()

@pytest.mark.asyncio
async def test_direct_resume_resets_rewind_and_handles_missing_record(tmp_path):
    registry = CommandRegistry()
    register_all_commands(registry)
    command = registry.find("resume")
    sm = SessionManager(str(tmp_path))
    saved = sm.create()
    saved.append(Message(role="user", content="old context"))
    saved.close()
    current = sm.create()
    tool = SimpleNamespace(file_history=object())
    agent = SimpleNamespace(work_dir=str(tmp_path), _loop_count=9,
                            clear_active_skills=Mock(), file_history=object(),
                            registry=SimpleNamespace(list_tools=lambda: [tool]))
    state = {}
    ctx = CommandContext(saved.session_id, agent, ConversationManager(), current, sm, None, Mock(), {
        "set_session": lambda value: state.update(session=value),
        "set_conversation": lambda value: state.update(conversation=value),
        "render_restored": AsyncMock(),
    })
    await command.handler(ctx)
    assert agent._loop_count == 0
    agent.clear_active_skills.assert_called_once()
    assert tool.file_history is agent.file_history
    assert saved.session_id in str(agent.file_history._session_dir)
    state["session"].close()
    current = sm.create()
    ctx.session = current
    state.clear()
    (tmp_path / ".YL1203Code" / "sessions" / (saved.session_id + ".jsonl")).unlink()
    ctx.args = saved.session_id
    await command.handler(ctx)
    assert not state
    assert not current._file.closed
    current.close()


@pytest.mark.asyncio
async def test_summary_finishing_after_switch_updates_original_session(tmp_path, monkeypatch):
    from YL1203Code import app as app_module
    sm = SessionManager(str(tmp_path))
    old = sm.create()
    new = sm.create()
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(*args):
        started.set()
        await release.wait()
        return "original summary"

    monkeypatch.setattr(app_module, "generate_session_summary", summarize)
    fake = SimpleNamespace(session=old, client=object(), agent=SimpleNamespace(protocol="anthropic"),
                           conversation=ConversationManager())
    task = asyncio.create_task(YL1203CodeApp._update_session_summary(fake))
    await started.wait()
    fake.session = new
    release.set()
    await task
    assert old.meta.summary == "original summary"
    assert new.meta.summary != "original summary"
    old.close()
    new.close()
