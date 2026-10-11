 #YL1203Code

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from YL1203Code.config import ConfigError, load_config
from YL1203Code.hooks import HookConfigError, HookEngine, load_hooks
from YL1203Code.permissions import PermissionMode


def main() -> None:
    # 先确保 .YL1203Code/ 目录存在，否则下面写 debug.log 会因目录不存在而崩溃
    Path(".YL1203Code").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=".YL1203Code/debug.log",
        filemode="w",
    )

    parser = argparse.ArgumentParser(prog="YL1203Code", description="YL1203Code AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: execute the prompt and print the result to stdout",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json"],
        default="text",
        help="Output format for -p mode: 'text' (default) prints final text, 'stream-json' emits NDJSON events",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help="Start in remote mode: WebSocket server on 127.0.0.1:18888 with browser UI",
    )
    parser.add_argument("--doctor", action="store_true", help="Check configuration, tools and local proxy ports")
    parser.add_argument("--init", action="store_true", help="Create local configuration using a short wizard")
    parser.add_argument("--protocol", choices=["anthropic", "openai", "openai-compat"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    args = parser.parse_args()

    if args.init:
        from YL1203Code.onboarding import write_initial_config
        protocol = args.protocol or input("协议 [anthropic/openai/openai-compat]: ").strip()
        base_url = args.base_url or input("API 地址: ").strip()
        model = args.model or input("模型名称: ").strip()
        try:
            path = write_initial_config(os.getcwd(), protocol=protocol, base_url=base_url, model=model)
            print(f"配置已创建: {path}。请通过环境变量设置 API Key，再运行 --doctor。")
        except (OSError, ValueError) as exc:
            print(f"初始化失败: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if args.doctor:
        from YL1203Code.onboarding import doctor_report
        try:
            doctor_config = load_config()
        except ConfigError:
            doctor_config = None
        print("\n".join(doctor_report(os.getcwd(), doctor_config)))
        return

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    if args.p is not None:
        output_format = getattr(args, "output_format", "text")
        failed = asyncio.run(_run_prompt(config, permission_mode, hook_engine, args.p, output_format))
        if failed:
            sys.exit(1)
        return

    # Remote 模式：启动 WebSocket 服务器，浏览器访问 http://localhost:18888
    if args.remote:
        from YL1203Code.remote import RemoteServer

        server = RemoteServer(
            providers=config.providers,
            mcp_servers=config.mcp_servers,
            hook_engine=hook_engine,
            permission_mode=permission_mode,
            config=config,
        )
        asyncio.run(server.run())
        return

    from YL1203Code.app import YL1203CodeApp
    from YL1203Code.driver import NoAltScreenDriver

    app = YL1203CodeApp(
        providers=config.providers,
        permission_mode=permission_mode,
        mcp_servers=config.mcp_servers,
        hook_engine=hook_engine,
        enable_fork=config.enable_fork,
        enable_verification_agent=config.enable_verification_agent,
        worktree_config=config.worktree,
        teammate_mode=config.teammate_mode,
        enable_coordinator_mode=config.enable_coordinator_mode,
        driver_class=NoAltScreenDriver,
        sandbox_config=config.sandbox,
        execution_config=config.execution,
    )
    app.run()


async def _run_prompt(config, permission_mode, hook_engine, prompt: str, output_format: str = 'text') -> bool:
    from contextlib import aclosing
    from YL1203Code.agent import (
        CompactNotification, ErrorEvent, LoopComplete, PermissionRequest,
        PermissionResponse, RetryEvent, StreamText, ThinkingText, ToolResultEvent,
        ToolUseEvent, TurnComplete, UsageEvent,
    )
    from YL1203Code.client import create_client, resolve_context_window
    from YL1203Code.conversation import ConversationManager
    from YL1203Code.mcp import MCPManager
    from YL1203Code.runtime import create_runtime

    is_json = output_format == 'stream-json'
    def emit_json(obj: dict) -> None:
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    provider = config.providers[0]
    client = create_client(provider)
    await resolve_context_window(provider)
    runtime = create_runtime(config, permission_mode, os.getcwd(), client=client, hook_engine=hook_engine)
    agent = runtime.agent
    mcp_manager = None
    conv = ConversationManager()
    conv.add_user_message(prompt)
    start = time.monotonic()
    text_buf = ''
    tool_calls: list[dict] = []
    failed = False
    stop_reason = 'end_turn'
    total_turns = 0

    async def consume_run() -> None:
        nonlocal text_buf, failed, stop_reason, total_turns
        async with aclosing(agent.run(conv)) as stream:
            async for event in stream:
                if isinstance(event, StreamText):
                    text_buf += event.text
                    if is_json:
                        emit_json({'type': 'assistant', 'text': event.text})
                elif isinstance(event, ThinkingText) and is_json:
                    emit_json({'type': 'thinking', 'text': event.text})
                elif isinstance(event, ToolUseEvent):
                    tool_calls.append({'id': event.tool_id, 'name': event.tool_name, 'is_error': False})
                    if is_json:
                        emit_json({'type': 'tool_use', 'tool_name': event.tool_name,
                                   'tool_id': event.tool_id, 'args': event.arguments})
                elif isinstance(event, ToolResultEvent):
                    for call in tool_calls:
                        if call['id'] == event.tool_id:
                            call['is_error'] = event.is_error
                    if is_json:
                        emit_json({'type': 'tool_result', 'tool_name': event.tool_name,
                                   'tool_id': event.tool_id, 'output': event.output,
                                   'is_error': event.is_error, 'elapsed': round(event.elapsed, 3)})
                elif isinstance(event, UsageEvent) and is_json:
                    emit_json({'type': 'usage', 'input_tokens': event.input_tokens,
                               'output_tokens': event.output_tokens})
                elif isinstance(event, TurnComplete) and is_json:
                    emit_json({'type': 'turn_complete', 'turn': event.turn})
                elif isinstance(event, LoopComplete):
                    total_turns += event.total_turns
                    stop_reason = event.stop_reason
                    failed |= stop_reason != 'end_turn'
                elif isinstance(event, ErrorEvent):
                    failed = True
                    if is_json:
                        emit_json({'type': 'error', 'message': event.message})
                    else:
                        print(f'Error: {event.message}', file=sys.stderr, flush=True)
                elif isinstance(event, CompactNotification) and is_json:
                    emit_json({'type': 'compact', 'message': event.message})
                elif isinstance(event, RetryEvent) and is_json:
                    emit_json({'type': 'retry', 'reason': event.reason})
                elif isinstance(event, PermissionRequest) and not event.future.done():
                    event.future.set_result(PermissionResponse.DENY)

    try:
        for warning in runtime.warnings:
            print(warning, file=sys.stderr)
        async with asyncio.timeout(config.execution.timeout_seconds or None):
            if config.mcp_servers:
                mcp_manager = MCPManager()
                mcp_manager.load_configs(config.mcp_servers)
                connected = await mcp_manager.register_all_tools(runtime.registry)
                for error in connected.errors:
                    print(error, file=sys.stderr, flush=True)
                instructions = [f'## {server.name}\n{server.instructions}'
                                for server in connected.servers if server.instructions]
                if instructions:
                    conv.add_system_reminder('# MCP Server Instructions\n\n' + '\n\n'.join(instructions))
            # During the foreground run, include ordinary background task results too.
            drain = agent.notification_fn
            await consume_run()
            deadline = time.monotonic() + 180
            while not failed:
                notes = drain() if drain else []
                if notes:
                    for note in notes:
                        conv.add_system_reminder(note)
                    await consume_run()
                    continue
                if not any(task.status == 'running' for task in runtime.task_manager.list_tasks()):
                    break
                if time.monotonic() >= deadline:
                    failed = True
                    stop_reason = 'background_timeout'
                    break
                await asyncio.sleep(.1)
            if any(task.status == 'failed' for task in runtime.task_manager.list_tasks()):
                failed = True
                stop_reason = 'background_error'
    except TimeoutError:
        failed, stop_reason = True, 'timeout'
        if is_json:
            emit_json({'type': 'error', 'message': 'Request time limit reached'})
        else:
            print('Error: Request time limit reached', file=sys.stderr)
    except Exception as exc:
        failed, stop_reason = True, 'error'
        if is_json:
            emit_json({'type': 'error', 'message': str(exc)})
        else:
            print(f'Error: {exc}', file=sys.stderr)
    finally:
        try:
            for message in conv.history:
                runtime.session.append(message)
        finally:
            try:
                await runtime.close()
            finally:
                if mcp_manager is not None:
                    await mcp_manager.shutdown()

    if is_json:
        emit_json({'type': 'result', 'result': text_buf,
                   'duration_ms': int((time.monotonic() - start) * 1000),
                   'num_turns': total_turns, 'tool_calls': tool_calls,
                   'usage': {'input_tokens': agent.total_input_tokens,
                             'output_tokens': agent.total_output_tokens},
                   'stop_reason': stop_reason, 'success': not failed})
    else:
        print(text_buf, end='', flush=True)
    return failed


if __name__ == '__main__':
    main()
