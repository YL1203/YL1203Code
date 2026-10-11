"""Atomic local task checkpoints and conservative conversation recovery."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from YL1203Code.conversation import (
    ConversationManager, Message, ThinkingBlock, ToolResultBlock, ToolUseBlock,
)

log = logging.getLogger(__name__)
_TASK_ID = re.compile(r"^[0-9a-f]{8}$")


class TaskStore:
    def __init__(self, work_dir: str) -> None:
        self.root = Path(work_dir).resolve()
        self.directory = self.root / ".YL1203Code" / "tasks"
        self.directory.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict[str, Any]]:
        records = {}
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("version") != 1:
                    continue
                task_id = data.get("id", "")
                if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
                    continue
                if not isinstance(data.get("history", []), list):
                    continue
                if data.get("status") == "running":
                    data["status"] = "interrupted"
                records[task_id] = data
            except (OSError, ValueError, TypeError):
                log.warning("Ignoring unreadable task checkpoint: %s", path.name)
        return records

    def save(self, data: dict[str, Any]) -> None:
        task_id = data["id"]
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("Invalid task ID")
        path = self.directory / f"{task_id}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)


def checkpoint_history(agent: Any) -> list[dict[str, Any]]:
    conversation = getattr(agent, "_current_conversation", None)
    return [asdict(message) for message in conversation.history] if conversation else []


def recover_conversation(record: dict[str, Any]) -> ConversationManager:
    conversation = ConversationManager()
    for raw in record.get("history", []):
        conversation.history.append(Message(
            role=raw["role"], content=raw.get("content", ""),
            tool_uses=[ToolUseBlock(**block) for block in raw.get("tool_uses", [])],
            tool_results=[ToolResultBlock(**block) for block in raw.get("tool_results", [])],
            thinking_blocks=[ThinkingBlock(**block) for block in raw.get("thinking_blocks", [])],
        ))
    # Missing results do not prove a side effect did not happen. Never replay calls.
    repaired: list[Message] = []
    history = conversation.history
    for index, message in enumerate(history):
        repaired.append(message)
        if not message.tool_uses:
            continue
        following = history[index + 1] if index + 1 < len(history) else None
        returned = {block.tool_use_id for block in following.tool_results} if following else set()
        missing = [ToolResultBlock(
            block.tool_use_id,
            "Task interrupted; outcome unknown. Inspect current state before continuing; do not replay this call.",
            is_error=True,
        ) for block in message.tool_uses if block.tool_use_id not in returned]
        if missing:
            if following and following.tool_results:
                following.tool_results.extend(missing)
            else:
                repaired.append(Message("user", "", tool_results=missing))
    conversation.history = repaired
    if not conversation.history:
        conversation.add_user_message(record["task"])
    conversation.add_system_reminder(
        "Resume the interrupted task. Inspect current files and commands' effects first. "
        "Previously completed tool calls must not be replayed automatically.",
    )
    return conversation
