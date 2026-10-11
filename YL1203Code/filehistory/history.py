 #YL1203Code

from __future__ import annotations

import hashlib
import difflib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_SNAPSHOTS = 100


@dataclass
class Backup:
    backup_path: str
    version: int
    timestamp: float
    existed: bool = True


@dataclass
class Snapshot:
    message_index: int
    user_text: str
    backups: dict[str, Backup] = field(default_factory=dict)
    timestamp: float = 0.0


class FileHistory:

    def __init__(self, base_dir: str, session_id: str) -> None:
        self._session_dir = Path(base_dir) / ".YL1203Code" / "file-history" / session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._tracked: dict[str, int] = {}
        self._snapshots: list[Snapshot] = []
        self._existed: dict[tuple[str, int], bool] = {}
        self._lock = threading.Lock()

    def _backup_name(self, file_path: str, version: int) -> str:
        h = hashlib.sha256(file_path.encode()).hexdigest()[:16]
        return f"{h}@v{version}"

    def track_edit(self, path: str) -> None:
        with self._lock:
            abs_path = str(Path(path).resolve())
            ver = self._tracked.get(abs_path, 0)
            new_ver = ver + 1

            try:
                data = Path(abs_path).read_bytes()
                bp = self._session_dir / self._backup_name(abs_path, new_ver)
                bp.write_bytes(data)
                self._existed[abs_path, new_ver] = True
            except FileNotFoundError:
                self._existed[abs_path, new_ver] = False

            self._tracked[abs_path] = new_ver

    def make_snapshot(self, msg_index: int, user_text: str) -> None:
        with self._lock:
            backups: dict[str, Backup] = {}
            for path, ver in self._tracked.items():
                bp = self._session_dir / (self._backup_name(path, ver) + f"-checkpoint-{time.time_ns()}")
                try:
                    bp.write_bytes(Path(path).read_bytes())
                    existed = True
                except FileNotFoundError:
                    existed = False
                backups[path] = Backup(
                    backup_path=str(bp), version=ver, timestamp=time.time(), existed=existed,
                )

            self._snapshots.append(Snapshot(
                message_index=msg_index,
                user_text=user_text,
                backups=backups,
                timestamp=time.time(),
            ))
            if len(self._snapshots) > MAX_SNAPSHOTS:
                self._snapshots = self._snapshots[-MAX_SNAPSHOTS:]

    def get_snapshots(self) -> list[Snapshot]:
        with self._lock:
            return list(self._snapshots)

    def has_snapshots(self) -> bool:
        with self._lock:
            return len(self._snapshots) > 0

    def _backups_at(self, snapshot_index: int | None) -> dict[str, Backup]:
        backups = {path: Backup(
            str(self._session_dir / self._backup_name(path, 1)), 1, 0,
            self._existed.get((path, 1), True),
        ) for path in self._tracked}
        if snapshot_index is not None:
            backups.update(self._snapshots[snapshot_index].backups)
        return backups

    def diff(self, snapshot_index: int | None = None) -> str:
        """Preview this session's edits, or edits relative to a checkpoint."""
        with self._lock:
            backups = self._backups_at(snapshot_index)
            chunks = []
            for path, backup in backups.items():
                try:
                    before = Path(backup.backup_path).read_text(encoding="utf-8", errors="replace") if backup.existed else ""
                    after = Path(path).read_text(encoding="utf-8", errors="replace") if Path(path).exists() else ""
                except OSError:
                    chunks.append(f"Cannot read change preview: {path}\n")
                    continue
                chunks.extend(difflib.unified_diff(
                    before.splitlines(keepends=True), after.splitlines(keepends=True),
                    fromfile=f"before/{path}", tofile=f"after/{path}",
                ))
            return "".join(chunks)

    def rewind(self, snapshot_index: int) -> list[str]:
        with self._lock:
            if snapshot_index < 0 or snapshot_index >= len(self._snapshots):
                return []

            changed: list[str] = []

            backups = self._backups_at(snapshot_index)
            # Verify every required backup before changing any file.
            data = {path: Path(backup.backup_path).read_bytes()
                    for path, backup in backups.items() if backup.existed}
            for file_path, backup in backups.items():
                if not backup.existed:
                    fp = Path(file_path)
                    if fp.exists():
                        fp.unlink()
                        changed.append(file_path)
                    continue
                backup_data = data[file_path]

                fp = Path(file_path)
                try:
                    current_data = fp.read_bytes()
                except FileNotFoundError:
                    current_data = b""

                if current_data != backup_data:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(backup_data)
                    changed.append(file_path)

            self._snapshots = self._snapshots[: snapshot_index + 1]
            for file_path, backup in backups.items():
                self._tracked[file_path] = backup.version

            return changed
