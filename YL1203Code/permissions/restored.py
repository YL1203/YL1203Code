from __future__ import annotations

from typing import Any

from YL1203Code.permissions.checker import Decision, PermissionChecker
from YL1203Code.permissions.modes import PermissionMode
from YL1203Code.permissions.rules import Rule, RuleEngine
from YL1203Code.permissions.sandbox import PathSandbox
from YL1203Code.tools.base import Tool


class SavedRuleEngine(RuleEngine):
    def __init__(self, rules: list[dict[str, str]]) -> None:
        super().__init__()
        self.rules = [Rule(**rule) for rule in rules]

    def _load_tiers(self) -> list[list[Rule]]:
        return [list(reversed(self.rules))]


class RestoredChecker(PermissionChecker):
    """A recovered task must satisfy both its saved and the current policy."""

    def __init__(self, current: PermissionChecker, saved: dict[str, Any]) -> None:
        narrowed = current.for_child(PermissionMode(saved["mode"]), str(current.sandbox.project_root))
        super().__init__(narrowed.detector, narrowed.sandbox, narrowed.rule_engine,
                         narrowed.mode, sandbox_enabled=narrowed.sandbox_enabled)
        self.current = current
        self.saved = PermissionChecker(
            current.detector,
            PathSandbox(saved["root"], saved.get("extra_allowed"), saved.get("deny_write")),
            SavedRuleEngine(saved.get("rules", [])), PermissionMode(saved["mode"]),
        )

    def copy(self) -> RestoredChecker:
        checker = super().copy()
        checker.current = self.current.copy()
        checker.saved = self.saved.copy()
        return checker

    def rebind_work_dir(self, previous_dir: str, work_dir: str) -> None:
        self.current.rebind_work_dir(previous_dir, work_dir)
        self.sandbox = self.current.sandbox

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        rank = {"allow": 0, "ask": 1, "deny": 2}
        decisions = [self.current.check(tool, arguments), self.saved.check(tool, arguments)]
        return max(decisions, key=lambda decision: rank[decision.effect])

    def snapshot(self) -> dict[str, Any]:
        # Keep the older restrictions on another restart; current policy is checked anew.
        return self.saved.snapshot()
