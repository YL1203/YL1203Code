from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any
from collections.abc import Awaitable


async def await_cleanup(cleanup: Awaitable[None]) -> None:
    """Finish resource cleanup even if the caller is cancelled repeatedly."""
    task = asyncio.create_task(cleanup)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


def process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Stop a process group created by this application and reap its shell."""
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(proc.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()
