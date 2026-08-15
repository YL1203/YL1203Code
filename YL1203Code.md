# YL1203Code 项目

一个终端 AI 编程助手（terminal AI coding assistant）。提供交互式 TUI、非交互式单次执行（`-p`）、以及浏览器远程模式（`--remote`）。

## 技术栈

- **语言**：Python（`requires-python >= 3.11`）
- **TUI**：[Textual](https://textual.textualize.io/)（`>=2.1.0`）
- **LLM 协议**：`anthropic` / `openai` / `openai-compat`
- **其他依赖**：`mcp`、`pydantic`、`httpx`、`websockets`、`pyyaml`
- **构建/包管理**：[uv](https://docs.astral.sh/uv/) + hatchling

## 快速开始

```bash
uv sync                 # 安装依赖并建立可编辑安装
YL1203Code              # 交互式启动（也可用 .venv/Scripts/YL1203Code.exe）
YL1203Code -p "你的提示"   # 非交互：执行一次并打印结果
YL1203Code --remote     # 远程模式：0.0.0.0:18888 的 WebSocket 服务 + 浏览器 UI
```

首次运行需要配置 API key，放在 `.YL1203Code/config.yaml`（或 `~/.YL1203Code/config.yaml`），或通过 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 环境变量。

## 目录结构

```
YL1203Code/            # 主包
├── __main__.py        # CLI 入口（argparse）
├── app.py             # Textual TUI 主应用
├── agent.py           # 核心 Agent 循环（流式事件、工具调用、权限请求）
├── client.py          # LLM 客户端抽象（anthropic / openai / openai-compat）
├── config.py          # 配置加载（provider、MCP server、权限模式）
├── conversation.py    # 会话与消息管理
├── prompts.py         # 系统提示、环境上下文、计划模式提示
├── serialization.py   # 消息序列化（对齐各协议）
├── remote.py          # 远程模式（WebSocket 服务端）
├── agents/            # 子代理（subagent）加载、分叉、任务管理
├── commands/          # 斜杠命令（/clear /compact /plan /memory /mcp ...）
├── context/           # 上下文窗口管理
├── hooks/             # Hook 引擎（pre/post 事件、条件、executor）
├── mcp/               # MCP 客户端与工具封装
├── memory/            # 长期记忆（会话、召回、合并、指令文件加载）
├── permissions/       # 权限模式与规则检查
├── sandbox/           # 沙箱（bwrap / seatbelt）
├── skills/            # 技能（skill）加载与安装
├── teams/             # 多 Agent 团队协作
├── tools/             # 内置工具（bash/read_file/write_file/grep/glob/...）
└── worktree/          # git worktree 隔离
```

`tests/` 为 pytest 测试（`pytest-asyncio`），覆盖 agent、hooks、memory、permissions、skills、teams、worktree、MCP 等模块。

## 核心功能

- **Agent 循环**：流式输出、工具调用、权限请求（`PermissionRequest`）、Hook 事件、用量统计。
- **子代理（Subagents）**：加载、分叉（fork）、任务管理、通知。
- **斜杠命令**：`/help` `/clear` `/compact` `/plan` `/review` `/memory` `/mcp` `/permission` `/status` `/tasks` `/session` `/worktree` 等。
- **Hook 引擎**：在 pre/post 工具调用、事件上执行命令/提示/HTTP/子代理，支持条件匹配与拒绝（reject）。
- **长期记忆**：项目级 `<root>/.YL1203Code/memory/` 与用户级 `~/.YL1203Code/memory/`，含会话摘要与召回。
- **MCP**：连接外部 MCP server，将其工具接入内置工具注册表。
- **权限模式**：可配置的权限检查与规则（`permissions.yaml` / `permissions.local.yaml`）。
- **沙箱**：支持 bwrap / seatbelt 隔离。
- **技能（Skills）**：从 `.YL1203Code/skills/` 或 `~/.YL1203Code/skills/` 加载。
- **远程模式**：WebSocket 服务端 + 浏览器 UI（`--remote`）。

## 配置

- 配置文件：`.YL1203Code/config.yaml`（项目）或 `~/.YL1203Code/config.yaml`（用户），另有 `.YL1203Code/config.local.yaml` 本地覆盖。
- 权限规则：`.YL1203Code/permissions.yaml`、`.YL1203Code/permissions.local.yaml`。
- 指令文件：`YL1203Code.md` 与 `AGENTS.md`（发现顺序：用户全局 → 项目目录链 → 本地覆盖，详见 `memory/instructions.py`）。

## 代码规范

- commit message 用英文。
- 变量命名用 snake_case。
- 模块、类、函数命名遵循 [PEP 8](https://peps.python.org/pep-0008/)。
