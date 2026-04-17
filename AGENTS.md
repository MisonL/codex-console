# 仓库规范指南

## 项目结构与模块组织

`webui.py` 是本地启动的入口点，用于启动基于 FastAPI 的 Web UI 界面。应用程序的核心代码均位于 `src/` 目录下：`src/web/` 包含应用工厂、路由、WebSocket 处理以及异步任务管理；`src/core/` 包含了核心注册逻辑、HTTP 客户端、OpenAI 特定流程以及上传链路；`src/services/` 实现了各邮箱服务提供商以及 Outlook 集成；`src/database/` 和 `src/config/` 分别负责数据持久化模型与配置。前端静态资源和模板分别放置在 `static/` 和 `templates/` 中。所有的测试用例位于 `tests/` 目录下，并采用了以功能为导向的命名方式（如 `test_registration_engine.py`）。

## 构建、测试与开发命令

使用 `uv sync` 或 `pip install -r requirements.txt` 安装相关依赖。在本地启动应用，请执行 `python webui.py`；若需开启热重载和调试日志，请使用 `python webui.py --debug`；如果需要修改监听地址，可使用 `python webui.py --host 0.0.0.0 --port 8080`。运行全量测试套件请使用 `pytest`。如果需要针对特定文件进行检查，可以运行 `pytest tests/test_static_asset_versioning.py`。如需打包生成可执行文件，Linux/macOS 下请运行 `bash build.sh`，Windows 下请运行 `build.bat`。

## 编码规范与命名约定

请遵循项目现有的 Python 编码风格：使用 4 空格缩进，函数和模块使用 `snake_case`（蛇形命名法），类名使用 `PascalCase`（帕斯卡命名法），如果代码逻辑不够直观，请提供简明扼要的文档字符串（docstrings）。导包顺序请遵循：标准库、第三方库、本地模块进行分组。当前仓库未在 `pyproject.toml` 中强制声明特定的代码格式化工具或检查器（linter），因此请尽量保持与周围现有代码风格一致，避免提交大量纯格式调整的代码。

## 测试指南

所有的自动化测试均使用 `pytest` 进行。请将测试文件添加到 `tests/` 目录下，不要在 `src/` 目录下存放测试代码。测试文件命名必须为 `test_*.py`，测试函数名需体现出行为导向，例如：`test_check_sentinel_sends_non_empty_pow`。针对 HTTP 请求与邮件相关流程，推荐使用替身（fakes）或预置队列响应的确定性单元测试。任何涉及路由变更、静态资源版本控制以及核心注册链路的修改，都必须补充相应的回归测试用例。

## 提交记录与 Pull Request 指南

参考最近的提交历史，提交信息使用了简短、职责单一的描述，例如：`适配子域`、`适配cloud-mail` 以及 `Fix release assets upload`。请将每个提交（Commit）的范围控制在单一的改动内，并提供简明的祈使句摘要。提交 PR 时，请说明更改的具体行为逻辑，列出你所运行的验证命令，关联相关的 Issue；如果修改了 `templates/` 或 `static/` 而导致了 Web UI 界面的变更，请附上相关截图。

## 安全与配置提示

在本地进行配置时，请将 `.env.example` 复制为 `.env`，并优先使用环境变量来存储敏感的密钥信息。严禁在代码库中提交真实的访问密码、各类 Token 或真实的数据库连接 URL。当测试 PostgreSQL 时，请配置 `APP_DATABASE_URL` 环境变量；否则系统将回退使用默认的 SQLite 数据库路径。