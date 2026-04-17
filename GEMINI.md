# 项目概览：codex-console

`codex-console` 是一个功能强大且经过增强的 OpenAI 账号管理控制台，涵盖了注册、身份验证、Token 管理以及订阅状态检查等功能。本项目是 `cnlimiter/codex-manager` 的持续修复与增强版本，专门针对 OpenAI 注册与登录链路的频繁变动进行了优化。

## 核心技术栈
- **后端:** Python 3.10+, FastAPI (Web 框架), Uvicorn (ASGI 服务器)。
- **客户端/网络:** `curl-cffi`（用于模拟浏览器指纹的高级 HTTP 请求）、`websockets`。
- **数据库:** SQLAlchemy (ORM), Alembic (数据库迁移), SQLite (默认), PostgreSQL (支持)。
- **前端:** Jinja2 (模板), 原生 JavaScript, CSS, 通过 WebSocket 实现实时日志。
- **自动化/工具:** Playwright (用于支付/绑卡流程), PyInstaller (用于打包可执行文件), UV (推荐的包管理工具)。

## 项目架构
- `webui.py`: 应用程序的主入口。
- `src/`: 核心源码目录。
    - `src/web/`: FastAPI 应用实现，包含路由（`accounts` 账号, `registration` 注册, `payment` 支付, `tasks` 任务, `selfcheck` 自检）以及用于后台任务的 `task_manager`。
    - `src/core/`: 核心业务逻辑。
        - `src/core/register.py`: 核心注册流程逻辑。
        - `src/core/auto_registration.py`: 自动注册编排。
        - `src/core/system_selfcheck.py`: 系统自检与修复逻辑。
        - `src/core/openai/`: OpenAI 特定流程，如 `codex_auth_workbench`、`browser_bind.py` 和 `payment.py`。
    - `src/database/`: SQLAlchemy 模型 (`models.py`)、CRUD 操作 (`crud.py`) 和会话管理。
    - `src/services/`: 外部服务集成，主要是多种邮箱服务商的实现（`cloud_mail`, `luckmail`, `duck_mail` 等）。
    - `src/config/`: 基于 `pydantic-settings` 的配置处理。
- `any-auto-register-clone/`: 集成的核心注册引擎。
- `alembic/`: 数据库迁移脚本与配置。
- `static/` & `templates/`: 前端静态资源与 HTML 模板。
- `tests/`: 针对路由、核心逻辑及集成的完整测试套件。

## 构建与运行

### 开发环境
- **安装依赖:**
  ```bash
  pip install -r requirements.txt
  # 或者使用 uv（推荐）
  uv sync --extra dev
  ```
- **启动 Web UI:**
  ```bash
  python webui.py
  # 常用参数: --host 0.0.0.0 --port 8000 --access-password admin123 --debug
  ```

### Docker 部署
- **使用 Docker Compose:**
  ```bash
  docker-compose up -d
  ```
- **数据持久化:** 务必将 `data/` 目录映射到卷：`-v $(pwd)/data:/app/data`。

### 打包可执行文件 (PyInstaller)
- **Windows:** 运行 `build.bat`。
- **Linux/macOS:** 运行 `bash build.sh`。
- 产物位于 `dist/` 目录。

## 开发规范
- **异步操作:** 耗时任务（Token 刷新、批量验证、注册等）通过 `src/web/task_manager.py` 作为后台异步任务处理，并通过 WebSocket 提供实时反馈。
- **数据库迁移:** 任何模型改动必须通过 Alembic 进行。
  ```bash
  alembic revision --autogenerate -m "描述"
  alembic upgrade head
  ```
- **测试:** 在提交重大更改前务必运行测试。
  ```bash
  uv run python -m pytest tests/
  ```
- **鉴权:** 所有 `/api/*` 和 `/api/ws/*` 接口均受登录鉴权保护。首次启动检测到默认凭据将强制跳转至改密页面。

## 关键配置
- **环境变量:**
    - `APP_HOST`: 默认 `0.0.0.0`。
    - `APP_PORT`: 默认 `8000`。
    - `APP_ACCESS_PASSWORD`: Web UI 访问密钥。
    - `APP_DATABASE_URL`: 数据库连接字符串（SQLite 或 PostgreSQL）。
- **ChatGPT 注册模式:**
    - `有 RT` (Refresh Token): 完整的 OAuth 流程，落库所有凭据。
    - `无 RT` (仅 Access Token): 仅通过会话复用获取 Token。

## 相关链接
- **GitHub:** [https://github.com/dou-jiang/codex-console](https://github.com/dou-jiang/codex-console)
- **博客:** [https://blog.cysq8.cn/](https://blog.cysq8.cn/)
