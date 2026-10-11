"""Opt-in Windows command isolation using an existing local Linux Docker image."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from YL1203Code.sandbox import Sandbox, SandboxConfig, protected_existing_paths


class DockerSandbox(Sandbox):
    command_platform = "linux"
    command_work_dir = "/workspace"
    def __init__(self, image: str | None = None) -> None:
        self.image = image or os.environ.get("YL1203CODE_SANDBOX_IMAGE", "python:3.11-slim")
        if self.image.startswith("-") or any(c.isspace() for c in self.image):
            raise ValueError("Invalid sandbox image name")

    def available(self) -> bool:
        docker = shutil.which("docker")
        if not docker:
            return False
        try:
            result = subprocess.run([docker, "info", "--format", "{{.OSType}}"],
                                    capture_output=True, text=True, timeout=3)
            if result.returncode or result.stdout.strip() != "linux":
                return False
            result = subprocess.run([docker, "image", "inspect", self.image],
                                    capture_output=True, timeout=3)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def wrap(self, command: str, config: SandboxConfig) -> str:
        raise RuntimeError("Docker sandbox requires explicit argv and a bound work directory")

    def execution_args(self, command: str, config: SandboxConfig, work_dir: str) -> list[str]:
        root = Path(work_dir).resolve()
        if not root.is_dir() or not any(root == Path(p).resolve() for p in config.allow_write):
            raise ValueError("Sandbox working directory must be explicitly writable")
        if "," in str(root):
            raise ValueError("Docker bind paths cannot contain commas")
        protected = protected_existing_paths(config.deny_write)
        args = [shutil.which("docker") or "docker", "run", "--pull", "never", "--rm", "--init",
                "--name", "yl1203code-" + uuid.uuid4().hex, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "512m",
                "--cpus", "2", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m"]
        if not config.network_enabled:
            args.extend(["--network", "none"])
        readonly = any(root == path or root.is_relative_to(path) for path in protected)
        args.extend(["--mount", f"type=bind,source={root},target=/workspace" + (",readonly" if readonly else "")])
        if not readonly:
            for path in protected:
                if not path.is_relative_to(root):
                    continue  # Outside paths are not exposed to the container.
                if "," in str(path):
                    raise ValueError("Docker bind paths cannot contain commas")
                relative = path.relative_to(root).as_posix()
                args.extend(["--mount", f"type=bind,source={path},target=/workspace/{relative},readonly"])
        args.extend(["--workdir", "/workspace", "--env", "HOME=/tmp", "--entrypoint", "/bin/bash",
                     self.image, "-c", command])
        return args

    async def cleanup(self, args: list[str] | None) -> None:
        if not args or "--name" not in args:
            return
        name = args[args.index("--name") + 1]
        process = await asyncio.create_subprocess_exec(
            args[0], "rm", "--force", name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, error = await asyncio.wait_for(process.communicate(), 10)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode and b"No such container" not in error:
            raise RuntimeError(f"Could not remove sandbox container {name}; inspect Docker before continuing")
