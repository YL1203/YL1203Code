 #YL1203Code
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from YL1203Code.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False
        # 存储 MCP 服务器的 InitializeResult，用于提取 instructions 等元信息
        self._init_result: types.InitializeResult | None = None


    async def _await_with_timeout(self, coro: Any, operation: str) -> Any:
        """在超时保护下等待一个协程，避免 MCP 服务器挂起时无限阻塞。

        ``timeout`` 为 ``None`` 或 ``<= 0`` 表示不设超时（保持旧行为）。
        超时时抛出带说明的 ``TimeoutError``，由调用方统一按异常处理。
        """
        timeout = self.config.timeout
        if not timeout or timeout <= 0:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"MCP '{self.name}' {operation} timed out after {timeout}s"
            ) from None


    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def instructions(self) -> str:
        """返回 MCP 服务器的 instructions（来自 InitializeResult）。"""
        if self._init_result is not None and self._init_result.instructions:
            return self._init_result.instructions
        return ""


    async def connect(self) -> None:
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            if self.config.is_stdio:
                read, write = await self._connect_stdio()
            else:
                read, write = await self._connect_http()

            session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            # 保存 InitializeResult，后续可从中提取 instructions
            self._init_result = await self._await_with_timeout(
                session.initialize(), "initialize"
            )
            self._session = session
            self._alive = True
            logger.info("MCP server '%s' connected", self.name)
        except Exception:
            await self._cleanup_stack()
            raise


    async def _connect_stdio(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        devnull = open(os.devnull, "w")
        self._stack.callback(devnull.close)
        read, write = await self._await_with_timeout(
            self._stack.enter_async_context(stdio_client(params, errlog=devnull)),
            "connect",
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx.AsyncClient(
            headers=resolved_headers,
            follow_redirects=True,
        )
        await self._stack.enter_async_context(http_client)

        result = await self._await_with_timeout(
            self._stack.enter_async_context(
                streamable_http_client(self.config.url, http_client=http_client)
            ),
            "connect",
        )
        read, write = result[0], result[1]
        return read, write


    async def list_tools(self) -> list[types.Tool]:
        assert self._session is not None
        result = await self._await_with_timeout(
            self._session.list_tools(), "list_tools"
        )
        return list(result.tools)


    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        assert self._session is not None
        return await self._await_with_timeout(
            self._session.call_tool(name, arguments), "call_tool"
        )

    async def close(self) -> None:
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug("Cancel scope cleanup (expected during shutdown): %s", e)
                else:
                    raise
            except Exception:
                logger.debug("Error closing stack for '%s'", self.name, exc_info=True)
            self._stack = None
