 #YL1203Code
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from YL1203Code.tools.base import Tool, ToolResult


class QuestionItem(BaseModel):
    type: str = Field(description="Question type: text, radio, select, checkbox")
    name: str = Field(description="Question identifier")
    message: str = Field(description="Question text to display")
    options: list[str] = Field(
        default_factory=list,
        description="Options for radio/select/checkbox types",
    )


class AskUserParams(BaseModel):
    questions: list[QuestionItem] = Field(
        description="List of questions to ask the user", min_length=1,
    )


class AskUserEvent:


    def __init__(
        self,
        questions: list[dict[str, Any]],
        future: asyncio.Future[dict[str, str] | None],
    ) -> None:
        self.questions = questions
        self.future = future


class AskUserTool(Tool):
    name = "AskUserQuestion"
    description = (
        "Ask the user one or more questions when you need information "
        "that cannot be determined from code or context alone. Supports "
        "text input, radio (single select), select, and checkbox (multi select) "
        "question types."
    )
    params_model = AskUserParams
    category: str = "read"
    is_system_tool = True
    should_defer = True


    def __init__(
        self, on_request: Callable[[AskUserEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._on_request = on_request
        self._pending_event: AskUserEvent | None = None

    async def execute(self, params: AskUserParams) -> ToolResult:
        if self._on_request is None:
            return ToolResult(output="User questions require an interactive UI", is_error=True)
        questions_data = [q.model_dump() for q in params.questions]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, str] | None] = loop.create_future()

        self._pending_event = AskUserEvent(questions=questions_data, future=future)

        try:
            # Present the question before waiting for its result.
            async with asyncio.timeout(300):
                await self._on_request(self._pending_event)
                answers = await future
        except asyncio.TimeoutError:
            return ToolResult(
                output="User did not respond within 5 minutes", is_error=True
            )
        finally:
            if not future.done():
                future.cancel()
            self._pending_event = None

        if answers is None:
            return ToolResult(output="User cancelled the questions", is_error=True)
        lines = []
        for q in params.questions:
            answer = answers.get(q.name, "(no answer)")
            lines.append(f"{q.name}: {answer}")

        return ToolResult(output="\n".join(lines))
