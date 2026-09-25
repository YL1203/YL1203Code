# YL1203Code 项目指令

这是一个 Python 3.11+ AI 编程助手。主包位于 `YL1203Code/`，测试位于仓库根目录的 `tests/`。

- 使用说明、配置示例和架构概览见 `README.md`。
- 开发与测试约定见 `AGENTS.md`。
- 从仓库根目录执行 `uv run YL1203Code` 和 `uv run pytest`。
- 主入口为 `__main__.py`；终端界面为 `app.py`；核心循环为 `agent.py`；模型适配为 `client.py`。
- 工具修改需同步考虑权限、Hooks、取消处理和工具结果回填。
- `.YL1203Code/` 保存本地配置、会话和运行状态，不应提交到 Git。
- 内置 Agent 的 Markdown 定义是运行资源，不能作为普通临时文档删除。
