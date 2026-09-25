# Repository Guidelines

## Project Structure & Module Organization

`YL1203Code/` is the main Python package. Its top-level modules contain the CLI (`__main__.py`), Textual app (`app.py`), agent loop, configuration, provider clients, and serialization. Feature packages such as `commands/`, `hooks/`, `memory/`, `permissions/`, `teams/`, `tools/`, and `worktree/` group related behavior. Tests live in `tests/` and mirror these areas with files such as `test_agent.py`. Project metadata is in `pyproject.toml`; local runtime state belongs under `.YL1203Code/`.

## Build, Test, and Development Commands

- `uv sync --group dev` installs runtime and test dependencies.
- `uv run YL1203Code` starts the interactive terminal UI.
- `uv run YL1203Code -p "your prompt"` runs a non-interactive request.
- `uv run YL1203Code --remote` starts the WebSocket/browser mode on port 18888.
- `uv run pytest` runs the complete test suite.
- `uv run pytest tests/test_permissions.py -q` runs one focused module.
- `uv build` builds wheel and source distributions through Hatchling.

Run commands from the repository root.

## Coding Style & Naming Conventions

Target Python 3.11+ and follow PEP 8 with four-space indentation. Use `snake_case` for modules, functions, variables, and tests; `PascalCase` for classes; and `UPPER_CASE` for constants. Add type annotations to public functions and preserve the existing `from __future__ import annotations` pattern. Follow surrounding asynchronous patterns. No formatter or linter is configured, so match nearby code and group imports as standard library, third party, then local.

## Testing Guidelines

Tests use pytest and `pytest-asyncio`; mark coroutine tests with `@pytest.mark.asyncio`. Name files `test_<feature>.py` and tests `test_<behavior>`. Place regression coverage in the closest existing suite. Use `tmp_path` and mocks to avoid real network or provider calls. No coverage threshold is declared, but changes should exercise success and failure paths.

## Commit & Pull Request Guidelines

History is sparse and uses short summaries (for example, `修改规范`). Keep commits focused and describe the observable change in the subject. Pull requests should explain the problem and solution, list validation commands, and link issues. Include screenshots or terminal captures for visible TUI/browser changes, and call out configuration or compatibility changes.

## Security & Configuration

Keep API keys in environment variables such as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, or in ignored `.YL1203Code/config.local.yaml` files. Never commit credentials, debug logs, local permission overrides, or generated environments.
