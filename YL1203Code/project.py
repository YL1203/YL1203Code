from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from YL1203Code.tools.base import SKIP_DIRS


@dataclass
class ProjectInfo:
    root: str
    key_files: list[str]
    languages: dict[str, int]
    test_commands: list[str]

    def summary(self) -> str:
        lines = [f"项目目录: {self.root}"]
        if self.key_files:
            lines.append("项目文件: " + ", ".join(self.key_files))
        if self.languages:
            lines.append("代码概况: " + ", ".join(f"{name} ({count})" for name, count in self.languages.items()))
        if self.test_commands:
            lines.extend(["建议测试命令:", *self.test_commands])
        else:
            lines.append("未识别测试入口，可使用 /test <命令> 指定。")
        return "\n".join(lines)


def discover_project(work_dir: str, *, command_platform: str | None = None) -> ProjectInfo:
    root = Path(work_dir).resolve()
    container = command_platform == "linux"
    windows = os.name == "nt" and not container
    known = ("pyproject.toml", "pytest.ini", "requirements.txt", "package.json",
             "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml", "go.mod",
             "README.md", "AGENTS.md")
    key_files = [name for name in known if (root / name).is_file()]
    suffixes = {".py": "Python", ".java": "Java", ".js": "JavaScript", ".ts": "TypeScript",
                ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust"}
    counts: Counter[str] = Counter()
    inspected = 0
    pending = [root]
    while pending and inspected < 2000:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    inspected += 1
                    if entry.is_symlink():
                        pass
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                            pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        language = suffixes.get(Path(entry.name).suffix)
                        if language:
                            counts[language] += 1
                    if inspected >= 2000:
                        break
        except OSError:
            continue
    commands: list[str] = []
    if ("pyproject.toml" in key_files or "pytest.ini" in key_files
            or (counts.get("Python") and (root / "tests").is_dir())):
        args = ["python" if container else sys.executable, "-m", "pytest"]
        commands.append("uv run pytest" if (root / "uv.lock").is_file() else
                        subprocess.list2cmdline(args) if windows else shlex.join(args))
    if "pom.xml" in key_files:
        wrapper = "mvnw.cmd" if windows else "mvnw"
        commands.append((".\\mvnw.cmd" if windows else "./mvnw") + " test"
                        if (root / wrapper).is_file() else "mvn test")
    if "build.gradle" in key_files or "build.gradle.kts" in key_files:
        wrapper = "gradlew.bat" if windows else "gradlew"
        commands.append((".\\gradlew.bat" if windows else "./gradlew") + " test"
                        if (root / wrapper).is_file() else "gradle test")
    if "package.json" in key_files:
        try:
            package = root / "package.json"
            data = json.loads(package.read_text(encoding="utf-8")) if package.stat().st_size < 1_000_000 else {}
            if data.get("scripts", {}).get("test"):
                commands.append("pnpm test" if (root / "pnpm-lock.yaml").is_file() else "npm test")
        except (OSError, ValueError, AttributeError):
            pass
    if "Cargo.toml" in key_files:
        commands.append("cargo test")
    if "go.mod" in key_files:
        commands.append("go test ./...")
    return ProjectInfo(str(root), key_files, dict(counts), commands)
