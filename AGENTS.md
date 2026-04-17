# Repository Guidelines

## Project Structure & Module Organization

`webui.py` is the local entry point and starts the FastAPI-based Web UI. Core application code lives under `src/`: `src/web/` contains the app factory, routes, WebSocket handling, and task management; `src/core/` holds registration, HTTP, OpenAI, and upload flows; `src/services/` implements mailbox providers and Outlook integrations; `src/database/` and `src/config/` cover persistence and settings. Frontend assets are split between `templates/` and `static/`. Tests live in `tests/` and follow feature-oriented filenames such as `test_registration_engine.py`.

## Build, Test, and Development Commands

Install dependencies with `uv sync` or `pip install -r requirements.txt`. Start the app locally with `python webui.py`; use `python webui.py --debug` for reload and debug logs, or `python webui.py --host 0.0.0.0 --port 8080` to change binding. Run the full test suite with `pytest`. For targeted checks, run a single file such as `pytest tests/test_static_asset_versioning.py`. Build packaged binaries with `bash build.sh` on Linux/macOS or `build.bat` on Windows.

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, `snake_case` for functions and modules, `PascalCase` for classes, and concise docstrings where behavior is not obvious. Keep imports grouped as standard library, third-party, then local modules. The repository does not currently declare a formatter or linter in `pyproject.toml`, so match surrounding code closely and avoid broad style-only diffs.

## Testing Guidelines

Use `pytest` for all automated tests. Add tests under `tests/`, not inside `src/`. Name files `test_*.py` and keep test names behavior-focused, for example `test_check_sentinel_sends_non_empty_pow`. Prefer deterministic unit tests with fakes or queued responses for HTTP and mail workflows. Any change touching routes, static asset versioning, or registration flows should include a focused regression test.

## Commit & Pull Request Guidelines

Recent history uses short, single-purpose subjects such as `适配子域`, `适配cloud-mail`, and `Fix release assets upload`. Keep commits scoped to one change and write a brief imperative summary. PRs should explain the behavior change, list the verification commands you ran, link related issues, and include screenshots when `templates/` or `static/` changes affect the Web UI.

## Security & Configuration Tips

Copy `.env.example` to `.env` for local configuration and prefer environment variables for secrets. Do not commit real access passwords, tokens, or database URLs. When testing PostgreSQL, set `APP_DATABASE_URL`; otherwise the default SQLite path is used.
