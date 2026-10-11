from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input

from YL1203Code.agent import PermissionRequest, PermissionResponse
from YL1203Code.app import YL1203CodeApp
from YL1203Code.permission_dialog import InlinePermissionWidget


class PermissionApp(App):
    _handle_permission_request = YL1203CodeApp._handle_permission_request
    on_inline_permission_widget_responded = (
        YL1203CodeApp.on_inline_permission_widget_responded
    )

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-area")
        yield Input(id="chat-input")


@pytest.mark.asyncio
async def test_consecutive_file_approvals_remove_previous_prompt_before_continuing():
    app = PermissionApp()
    loop = asyncio.get_running_loop()
    first = PermissionRequest("WriteFile", "verify_add.py", loop.create_future())
    second = PermissionRequest("WriteFile", "verify_check.py", loop.create_future())
    second_shown = asyncio.Event()

    async def request_both():
        await app._handle_permission_request(first)
        assert await first.future is PermissionResponse.ALLOW
        await app._handle_permission_request(second)
        second_shown.set()
        assert await second.future is PermissionResponse.DENY

    async with app.run_test(size=(100, 30)) as pilot:
        task = asyncio.create_task(request_both())
        try:
            await pilot.pause()
            app.query_one(InlinePermissionWidget).action_select()
            waiter = asyncio.create_task(second_shown.wait())
            try:
                await asyncio.wait(
                    {task, waiter}, timeout=2, return_when=asyncio.FIRST_COMPLETED
                )
                if task.done():
                    await task
                assert second_shown.is_set(), "Next approval was not displayed"
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            prompts = list(app.query(InlinePermissionWidget))
            assert len(prompts) == 1
            assert prompts[0]._description == "verify_check.py"
            prompts[0].action_deny()
            await asyncio.wait_for(task, timeout=2)
            await pilot.pause()
            assert not list(app.query(InlinePermissionWidget))
            assert not app.query_one("#chat-input").disabled
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)



@pytest.mark.asyncio
async def test_new_approval_waits_for_cancelled_prompt_removal():
    app = PermissionApp()
    loop = asyncio.get_running_loop()
    first = PermissionRequest("WriteFile", "cancelled.py", loop.create_future())
    second = PermissionRequest("WriteFile", "next.py", loop.create_future())

    async with app.run_test(size=(100, 30)) as pilot:
        await app._handle_permission_request(first)
        old_widget = app.query_one(InlinePermissionWidget)
        first.future.cancel()
        app._pending_perm_request = None
        old_widget.remove()

        await app._handle_permission_request(second)
        assert first.future.cancelled()
        assert not second.future.done()
        prompts = list(app.query(InlinePermissionWidget))
        assert len(prompts) == 1
        assert prompts[0]._description == "next.py"
        prompts[0].action_deny()
        assert await asyncio.wait_for(second.future, timeout=2) is PermissionResponse.DENY
        await pilot.pause()
        assert not app.query_one("#chat-input").disabled
