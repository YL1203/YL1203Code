from __future__ import annotations

import asyncio

import pytest
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from YL1203Code.app import ChatInput, YL1203CodeApp
from YL1203Code.askuser_dialog import InlineAskUserWidget
from YL1203Code.commands.completion import CompletionPopup
from YL1203Code.tools.ask_user import AskUserParams, AskUserTool


class InteractionApp(YL1203CodeApp):
    CSS_PATH = None
    INLINE = False
    def __init__(self):
        super().__init__(providers=[])

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-area"):
            yield Static("Copy this answer", id="answer")
        yield ChatInput(id="chat-input")
        yield CompletionPopup()

    async def on_mount(self, event):
        event.prevent_default()
        self.query_one(ChatInput).focus()

    def copy_to_clipboard(self, text: str) -> None:
        # Exercise selection and routing without changing the user's OS clipboard.
        self._clipboard = text


async def select_answer(app, pilot):
    await pilot.mouse_down("#answer", offset=(0, 0))
    await pilot.hover("#answer", offset=(8, 0))
    await pilot.mouse_up("#answer", offset=(8, 0))
    await pilot.pause()
    assert app.screen.get_selected_text() == "Copy this"


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [False, True])
async def test_ctrl_c_copies_output_without_exiting_or_cancelling(busy):
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        task = asyncio.create_task(asyncio.Event().wait())
        app._streaming = busy
        app._agent_task = task if busy else None
        try:
            await select_answer(app, pilot)
            await pilot.press("ctrl+c")
            assert app.clipboard == "Copy this"
            assert app.is_running
            assert not task.done()
            assert app._streaming is busy
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_ctrl_c_copies_input_and_does_not_exit_when_nothing_selected():
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        await pilot.press("ctrl+c")
        assert app.is_running
        inp = app.query_one(ChatInput)
        inp.load_text("draft answer")
        inp.select_all()
        await pilot.press("ctrl+c")
        assert app.clipboard == "draft answer"
        assert inp.text == "draft answer"


@pytest.mark.asyncio
async def test_right_mouse_copies_existing_selection():
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        await select_answer(app, pilot)
        answer = app.query_one("#answer")
        app.post_message(events.MouseDown(answer, 2, 0, 0, 0, 3, False, False, False))
        await pilot.pause()
        assert app.clipboard == "Copy this"
        assert app.screen.get_selected_text() == "Copy this"


@pytest.mark.asyncio
async def test_questions_show_immediately_and_return_named_answers():
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        tool = AskUserTool(on_request=app._handle_askuser)
        params = AskUserParams(questions=[dict(
            type="radio", name="page_type", message="Which page?",
            options=["Landing", "Dashboard"],
        )])
        for _ in range(2):
            task = asyncio.create_task(tool.execute(params))
            try:
                await pilot.pause()
                assert not task.done()
                assert len(app.query(InlineAskUserWidget)) == 1
                assert app.query_one(ChatInput).disabled
                await pilot.press("down", "enter")
                result = await asyncio.wait_for(task, timeout=2)
                assert result.output == "page_type: Dashboard"
                assert not result.is_error
                assert not list(app.query(InlineAskUserWidget))
                assert not app.query_one(ChatInput).disabled
                assert tool._pending_event is None
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_checkbox_and_text_answers_keep_schema_names():
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        tool = AskUserTool(on_request=app._handle_askuser)
        params = AskUserParams(questions=[
            dict(type="checkbox", name="features", message="Features?", options=["A", "B"]),
            dict(type="text", name="title", message="Title?"),
        ])
        task = asyncio.create_task(tool.execute(params))
        try:
            await pilot.pause()
            await pilot.press("space", "down", "space", "enter", "H", "i", "space", "X", "enter", "enter")
            result = await asyncio.wait_for(task, timeout=2)
            assert result.output == "features: A, B\ntitle: Hi X"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_task", [False, True])
async def test_question_cancellation_cleans_prompt_and_input(cancel_task):
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        tool = AskUserTool(on_request=app._handle_askuser)
        params = AskUserParams(questions=[dict(type="text", name="title", message="Title?")])
        task = asyncio.create_task(tool.execute(params))
        try:
            await pilot.pause()
            assert list(app.query(InlineAskUserWidget))
            if cancel_task:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await pilot.press("escape")
                result = await asyncio.wait_for(task, timeout=2)
                assert result.is_error
                assert "cancel" in result.output.lower()
            await pilot.pause()
            assert not list(app.query(InlineAskUserWidget))
            assert not app.query_one(ChatInput).disabled
            assert tool._pending_event is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_unwired_question_tool_returns_error_instead_of_waiting():
    tool = AskUserTool()
    result = await asyncio.wait_for(tool.execute(AskUserParams(questions=[
        dict(type="text", name="title", message="Title?"),
    ])), timeout=1)
    assert result.is_error


def test_empty_question_list_is_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AskUserParams(questions=[])







@pytest.mark.asyncio
async def test_ctrl_q_exits_explicitly():
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        await pilot.press("ctrl+q")
        assert not app.is_running


@pytest.mark.asyncio
async def test_ctrl_c_without_selection_interrupts_waiting_question():
    app = InteractionApp()
    async with app.run_test(size=(90, 25)) as pilot:
        tool = AskUserTool(on_request=app._handle_askuser)
        params = AskUserParams(questions=[dict(type="text", name="title", message="Title?")])
        task = asyncio.create_task(tool.execute(params))
        app._streaming = True
        app._agent_task = task
        try:
            await pilot.pause()
            await pilot.press("ctrl+c")
            assert task.cancelled()
            assert app.is_running
            assert not app._streaming
            assert not list(app.query(InlineAskUserWidget))
            assert not app.query_one(ChatInput).disabled
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_windows_clipboard_uses_unicode_without_shell(monkeypatch):
    import os
    import subprocess
    from types import SimpleNamespace

    if os.name != "nt":
        pytest.skip("Windows clipboard transport")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    app = SimpleNamespace(_clipboard="", notify=lambda *args, **kwargs: None)
    YL1203CodeApp.copy_to_clipboard(app, "中文 emoji 🐷\nline 2")
    assert app._clipboard == "中文 emoji 🐷\nline 2"
    assert calls[0][0] == (["clip.exe"],)
    assert calls[0][1]["input"].decode("utf-16") == "中文 emoji 🐷\nline 2"
    assert not calls[0][1].get("shell", False)


@pytest.mark.asyncio
async def test_question_timeout_cancels_pending_future(monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda _: real_timeout(0.01))
    requests = []

    async def present(event):
        requests.append(event)
        await event.future

    tool = AskUserTool(on_request=present)
    result = await tool.execute(AskUserParams(questions=[
        dict(type="text", name="title", message="Title?"),
    ]))
    assert result.is_error
    assert "5 minutes" in result.output
    assert requests[0].future.cancelled()
    assert tool._pending_event is None


def test_copy_on_older_textual_screen_does_not_crash():
    from types import SimpleNamespace
    app = SimpleNamespace(screen=SimpleNamespace(), focused=None)
    assert YL1203CodeApp._copy_selected_text(app) is False
