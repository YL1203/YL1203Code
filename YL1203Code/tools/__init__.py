 #YL1203Code
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from YL1203Code.tools.base import Tool

if TYPE_CHECKING:
    from YL1203Code.cache import FileCache


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()
        self._work_dir: str | None = None
        self._on_work_dir_changed: Callable[[str], None] | None = None

    def register(self, tool: Tool) -> None:
        if tool.name == "ToolSearch" and getattr(tool, "_registry", self) is not self:
            tool = copy.copy(tool)
            tool._registry = self
        if self._work_dir and tool.name in {
            "ReadFile", "WriteFile", "EditFile", "Glob", "Grep", "Bash",
            "EnterWorktree", "ExitWorktree",
        }:
            previous_dir = tool.work_dir
            tool = copy.copy(tool)
            tool.work_dir = self._work_dir
            if tool.name in {"EnterWorktree", "ExitWorktree"}:
                tool._set_work_dir = self._on_work_dir_changed
            config = getattr(tool, "sandbox_config", None)
            if config is not None and previous_dir:
                def rebase(path: str) -> str:
                    try:
                        relative = Path(path).relative_to(previous_dir)
                    except ValueError:
                        return path
                    return str(Path(self._work_dir) / relative)

                tool.sandbox_config = replace(
                    config,
                    allow_write=[rebase(path) for path in config.allow_write],
                    deny_write=[rebase(path) for path in config.deny_write],
                )
        self._tools[tool.name] = tool

    def bind_work_dir(
        self, work_dir: str, on_change: Callable[[str], None] | None = None,
    ) -> None:
        """Bind local tools without changing process cwd or another registry's tools."""
        self._work_dir = work_dir
        self._on_work_dir_changed = on_change
        for tool in list(self._tools.values()):
            self.register(tool)

    def copy(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry._tools = dict(self._tools)
        registry._disabled = set(self._disabled)
        registry._discovered = set(self._discovered)
        search = registry.get("ToolSearch")
        if search is not None:
            search = copy.copy(search)
            search._registry = registry
            registry._tools[search.name] = search
        return registry

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        self._disabled.discard(name)


    def disable(self, name: str) -> None:
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        self._disabled.clear()


    def mark_discovered(self, name: str) -> None:
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered


    def get_deferred_tool_names(self) -> list[str]:
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        ]

    def search_deferred(
        self, query: str, max_results: int, protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def find_deferred_by_names(
        self, names: list[str], protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not getattr(tool, "should_defer", False):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())


    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if getattr(tool, "should_defer", False) and name not in self._discovered:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                schemas.append(base)
        return schemas


def create_default_registry(file_cache: FileCache | None = None, file_history: Any = None) -> ToolRegistry:
    from YL1203Code.tools.bash import Bash
    from YL1203Code.tools.edit_file import EditFile
    from YL1203Code.tools.file_state_cache import FileStateCache
    from YL1203Code.tools.glob import Glob
    from YL1203Code.tools.grep import Grep
    from YL1203Code.tools.read_file import ReadFile
    from YL1203Code.tools.write_file import WriteFile

    file_state_cache = FileStateCache()

    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache, file_state_cache=file_state_cache))
    registry.register(WriteFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(EditFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    return registry
