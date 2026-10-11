from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable
from pathlib import Path

if TYPE_CHECKING:
    from YL1203Code.agent import Agent

log = logging.getLogger(__name__)


@dataclass
class ProgressInfo:
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_activity: str = ""


@dataclass
class BackgroundTask:
    id: str
    name: str
    agent: Agent
    task: str
    status: str = "running"
    result: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    cancel: Callable[[], None] | None = None
    progress: ProgressInfo = field(default_factory=ProgressInfo)


class TaskManager:


    def __init__(self, work_dir: str | None = None) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()
        self._async_tasks: dict[str, asyncio.Task[None]] = {}
        self._store = None
        self._saved: dict[str, dict[str, Any]] = {}
        if work_dir is not None:
            self.configure_storage(work_dir)

    def configure_storage(self, work_dir: str) -> None:
        from YL1203Code.agents.task_store import TaskStore
        self._store = TaskStore(work_dir)
        self._saved = self._store.load()

    def saved_tasks(self) -> list[dict[str, Any]]:
        return [dict(record) for key, record in self._saved.items() if key not in self._tasks]

    def _persist(self, bg: BackgroundTask, *, strict: bool = False) -> None:
        if self._store is None:
            return
        from YL1203Code.agents.task_store import checkpoint_history
        data = {
            "version": 1, "id": bg.id, "name": bg.name, "task": bg.task,
            "status": bg.status, "result": bg.result, "work_dir": bg.agent.work_dir,
            "instructions": bg.agent.instructions_content,
            "history": checkpoint_history(bg.agent),
            "input_tokens": bg.agent.total_input_tokens,
            "output_tokens": bg.agent.total_output_tokens,
            "allowed_tools": [tool.name for tool in bg.agent.registry.list_tools()
                              if bg.agent.registry.is_enabled(tool.name)],
            "policy": bg.agent.permission_checker.snapshot() if bg.agent.permission_checker else None,
        }
        try:
            self._store.save(data)
            self._saved[bg.id] = data
        except OSError:
            if strict:
                raise
            log.exception("Could not persist task %s", bg.id)

    def _finish(self, task_id: str, task: asyncio.Task | None = None) -> None:
        bg = self._tasks.get(task_id)
        if bg is None or bg.end_time is not None:
            return
        if task is not None and task.cancelled():
            bg.status = "cancelled"
            bg.result = "Task was cancelled"
        bg.end_time = time.monotonic()
        bg.progress.input_tokens = bg.agent.total_input_tokens
        bg.progress.output_tokens = bg.agent.total_output_tokens
        self._async_tasks.pop(task_id, None)
        self._persist(bg)
        self._notify_queue.put_nowait(task_id)

    def _callback_options(self, bg: BackgroundTask) -> dict[str, Any]:
        if self._store is None:
            return {}
        def checkpoint(event: dict[str, Any]) -> None:
            if event.get("type") == "tool_use":
                bg.progress.tool_call_count += 1
            self._persist(bg)
        return {"event_callback": checkpoint}

    def resume(self, task_id: str, parent_agent: Agent) -> str:
        from YL1203Code.agent import Agent as AgentClass
        from YL1203Code.agents.task_store import recover_conversation
        import copy

        record = self._saved.get(task_id)
        if record is None or task_id in self._tasks:
            raise ValueError("Task is not a saved task")
        if record.get("status") not in {"interrupted", "failed", "cancelled"}:
            raise ValueError("Only interrupted, failed or cancelled tasks can be resumed")
        directory = Path(record["work_dir"]).resolve()
        if not directory.is_dir() or not directory.is_relative_to(self._store.root):
            raise ValueError("Saved task workspace is unavailable or outside this project")
        registry = parent_agent.registry.copy()
        original_tools = set(record.get("allowed_tools", []))
        for tool in registry.list_tools():
            if tool.name not in original_tools or tool.name in {"Agent", "TeamCreate", "TeamDelete", "EnterWorktree", "ExitWorktree",
                             "ExitPlanMode", "AskUserQuestion", "InstallSkill"}:
                registry.disable(tool.name)
        checker = parent_agent.permission_checker
        if checker and record.get("policy"):
            from YL1203Code.permissions.restored import RestoredChecker
            checker = RestoredChecker(checker.copy(), record["policy"])
        agent = AgentClass(
            parent_agent.client, registry, parent_agent.protocol, work_dir=parent_agent.work_dir,
            permission_checker=checker.copy() if checker else None,
            max_iterations=parent_agent.max_iterations, max_total_tokens=parent_agent.max_total_tokens,
            timeout_seconds=parent_agent.timeout_seconds, context_window=parent_agent.context_window,
            instructions_content=record.get("instructions", parent_agent.instructions_content),
        )
        agent.work_dir = str(directory)
        loader = registry.get("LoadSkill")
        if loader:
            loader = copy.copy(loader)
            loader.set_agent(agent)
            registry.register(loader)
        new_id = self.launch(agent, record["task"], name=record.get("name", ""),
                             fork_conversation=recover_conversation(record))
        updated = {**record, "status": "resumed", "resumed_as": new_id}
        try:
            self._store.save(updated)
        except OSError:
            self.cancel(new_id)
            raise
        self._saved[task_id] = updated
        return new_id


    def launch(
        self,
        agent: Agent,
        task: str,
        name: str = "",
        fork_conversation: Any = None,
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task,
        )
        if fork_conversation is not None:
            agent._current_conversation = fork_conversation
        self._persist(bg, strict=True)
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(
            self._run_background(task_id, fork_conversation)
        )
        self._async_tasks[task_id] = async_task
        async_task.add_done_callback(lambda task: self._finish(task_id, task))

        bg.cancel = async_task.cancel
        return task_id


    async def _run_background(
        self, task_id: str, fork_conversation: Any = None
    ) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            if fork_conversation is not None:
                result = await bg.agent.run_to_completion("", fork_conversation, **self._callback_options(bg))
            else:
                result = await bg.agent.run_to_completion(bg.task, **self._callback_options(bg))
            bg.result = result
            bg.status = "completed"

            if bg.agent.team_name and bg.agent._team_manager:
                mailbox = bg.agent._team_manager.get_mailbox(bg.agent.team_name)
                if mailbox:
                    from YL1203Code.teams.mailbox import create_message
                    msg = create_message(
                        from_agent=bg.name,
                        to_agent="lead",
                        content=f"[idle] {bg.name}: completed initial task",
                        summary=f"{bg.name} idle",
                    )
                    mailbox.write("lead", msg)

                    for _ in range(60):
                        await asyncio.sleep(1)
                        msgs = mailbox.consume(bg.agent.agent_id)
                        if not msgs:
                            continue
                        prompt = "\n\n".join(
                            f"[Message from {m.from_agent}] {m.content}" for m in msgs
                        )
                        bg.status = "running"
                        self._persist(bg)
                        result = await bg.agent.run_to_completion(prompt, **self._callback_options(bg))
                        bg.result = result
                        bg.status = "completed"
                        msg = create_message(
                            from_agent=bg.name,
                            to_agent="lead",
                            content=f"[idle] {bg.name}: completed follow-up",
                            summary=f"{bg.name} idle",
                        )
                        mailbox.write("lead", msg)

        except asyncio.CancelledError:
            if bg.status != "completed":
                bg.status = "cancelled"
                bg.result = "Task was cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            self._finish(task_id)


    def adopt_running(
        self,
        agent: Agent,
        task_description: str,
        partial_result: str = "",
        name: str = "",
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task_description,
            result=partial_result,
        )
        self._tasks[task_id] = bg
        self._persist(bg)

        async_task = asyncio.create_task(self._continue_background(task_id))
        self._async_tasks[task_id] = async_task
        async_task.add_done_callback(lambda task: self._finish(task_id, task))
        bg.cancel = async_task.cancel
        return task_id


    async def _continue_background(self, task_id: str) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            result = await bg.agent.run_to_completion(bg.task, **self._callback_options(bg))
            bg.result = (bg.result + "\n" + result).strip() if bg.result else result
            bg.status = "completed"
        except asyncio.CancelledError:
            bg.status = "cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            self._finish(task_id)

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        bg = self._tasks.get(task_id)
        if bg is None or bg.status != "running":
            return False
        async_task = self._async_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            return True
        return False

    def poll_completed(self) -> list[BackgroundTask]:
        completed: list[BackgroundTask] = []
        while not self._notify_queue.empty():
            try:
                task_id = self._notify_queue.get_nowait()
                bg = self._tasks.get(task_id)
                if bg is not None:
                    completed.append(bg)
            except asyncio.QueueEmpty:
                break
        return completed
