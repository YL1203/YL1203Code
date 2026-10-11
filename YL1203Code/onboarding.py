from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from YL1203Code.config import AppConfig
from YL1203Code.sandbox import create_sandbox


def write_initial_config(
    work_dir: str, *, protocol: str, base_url: str, model: str,
) -> Path:
    if protocol not in {"anthropic", "openai", "openai-compat"}:
        raise ValueError("Unsupported protocol")
    if urlsplit(base_url).scheme not in {"http", "https"} or not model.strip():
        raise ValueError("A valid HTTP endpoint and model are required")
    key = "ANTHROPIC_API_KEY" if protocol == "anthropic" else "OPENAI_API_KEY"
    config = {
        "providers": [{"name": "default", "protocol": protocol, "base_url": base_url,
                       "model": model.strip(), "api_key": "${" + key + "}"}],
        "permission_mode": "default",
        "execution": {"max_iterations": 40, "max_total_tokens": 0, "timeout_seconds": 0},
    }
    path = Path(work_dir) / ".YL1203Code" / "config.local.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
    return path


def doctor_report(work_dir: str, config: AppConfig | None, *, config_error: str = "") -> list[str]:
    lines = [f"Python: {platform.python_version()}", f"系统: {platform.system()}"]
    for command in ("git", "uv", "node", "java", "mvn", "gradle"):
        lines.append(f"{command}: {'已安装' if shutil.which(command) else '未检测到'}")
    if config is None:
        # Validation errors can contain credential-bearing configuration values.
        lines.append("配置: 不可用，请检查配置文件，或运行 --init 创建配置。")
    else:
        lines.append("配置: 校验通过")
        for index, provider in enumerate(config.providers, 1):
            key_name = "ANTHROPIC_API_KEY" if provider.protocol == "anthropic" else "OPENAI_API_KEY"
            key = provider.resolve_api_key()
            present = bool(key and not key.startswith("${"))
            lines.append(f"模型 #{index}: {provider.protocol}; {key_name}: {'已设置' if present else '未设置'}")
    proxies = [os.environ.get(name, "") for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")]
    git = shutil.which("git")
    if git:
        try:
            result = subprocess.run([git, "config", "--get", "http.proxy"], cwd=work_dir,
                                    capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                proxies.append(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    seen = set()
    for proxy in proxies:
        if not proxy:
            continue
        try:
            parsed = urlsplit(proxy if "://" in proxy else "http://" + proxy)
            host, port = parsed.hostname, parsed.port
            if not host or (host, port) in seen:
                continue
            seen.add((host, port))
            state = "已配置"
            if host in {"127.0.0.1", "localhost", "::1"} and port:
                try:
                    with socket.create_connection((host, port), timeout=0.3):
                        state = "端口可连接"
                except OSError:
                    state = "端口无法连接，请核对代理软件的端口"
            lines.append(f"代理: {host}:{port or '默认'} — {state}")
        except ValueError:
            lines.append("代理: 地址格式无效")
    sandbox = create_sandbox()
    supported = sandbox is not None and sandbox.available()
    lines.append(f"命令 OS 沙箱: {'可用' if supported else '不可用（继续使用应用权限检查）'}")
    return lines
