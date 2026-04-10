# CR-REPO-DEEPCLEAN-2026-04-08

## 范围

- 注册主链路：AnyAuto 有 RT / 无 RT、Passwordless OAuth 接力、回调收口
- 调度：计划注册任务调度器
- 实时状态：TaskManager、注册页 WebSocket、批量取消联动
- 文档与仓库卫生：README、临时文件、忽略规则

## 关键发现与处理

1. `src/web/scheduler.py`
   - 问题：`_schedule_job()` 被声明为 `async def`，但在 `poll_due_jobs()` 中直接调用，导致到期任务不会真正创建后台协程。
   - 处理：改为同步方法，内部直接 `asyncio.create_task(self.run_job(job_uuid))`。

2. `src/web/routes/registration.py`
   - 问题：Outlook 批量任务预先把 `email_service_id` 绑定到数据库记录，但执行线程没有回读该绑定，实际运行时会重新扫描 Outlook 服务，导致“所选账户”和“实际执行账户”可能脱钩。
   - 处理：新增 `_resolve_task_email_service_id()`，显式优先使用请求参数，其次回退到任务表已绑定的 `email_service_id`。

3. `src/web/routes/registration.py` / `src/web/routes/websocket.py`
   - 问题：取消口径不一致。HTTP 单任务取消只改数据库，不通知 `TaskManager`；WebSocket 批量取消只改 `TaskManager`，不回写批次内存状态和子任务取消标记。
   - 处理：
     - 单任务取消改为同步写入 `TaskManager` 状态。
     - `pending` 任务立即落为 `cancelled`。
     - `running` 任务只标记为 `cancelling`，避免假装已经停止。
     - WebSocket 批量取消增加对 `batch_tasks` 和 `_cancel_batch_tasks()` 的联动。

4. `src/web/routes/registration.py`
   - 问题：计划任务和统一注册配置中的布尔字段如果来自旧配置字符串（如 `"false"`），原有 `bool("false")` 会被错误解析为 `True`。
   - 处理：新增 `_coerce_bool()`，统一兼容字符串 / 数字 / 布尔值。

## 仓库瘦身

- 删除明确的临时文件：
  - `tmp_app_core.js`
  - `tmp_redirectToPage.js`
  - `standalone_rt_test_debug.py`
  - `registration_output.log`
- 更新 `.gitignore`，补充：
  - `tmp_*.js`
  - `*_debug.py`
  - `registration_output.log`

## 回归验证

执行命令：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --with ruff ruff check \
  src/web/scheduler.py \
  src/web/routes/registration.py \
  src/web/routes/websocket.py \
  tests/test_registration_scheduling.py \
  tests/test_registration_engine.py \
  tests/test_registration_frontend_running_state.py

npm_config_cache=/tmp/npm-cache npx prettier@3 --check static/js/*.js static/css/style.css

uv run --group dev pytest \
  tests/test_registration_scheduling.py \
  tests/test_task_manager_websocket_history.py \
  tests/test_chatgpt_registration_mode.py \
  tests/test_registration_ui_mode_flag.py \
  tests/test_registration_frontend_running_state.py \
  tests/test_anyauto_auth_flow.py \
  tests/test_registration_wait_strategy.py -q
```

结果：

- `ruff check`: `All checks passed!`
- `prettier --check`: `All matched files use Prettier code style!`
- `pytest`: `32 passed in 7.32s`

新增覆盖：

- 调度器实际创建执行协程
- 任务绑定邮箱服务 ID 回退逻辑
- 旧布尔字符串兼容解析

## 未覆盖说明

- 当前环境没有系统级 `pytest` / `ruff` / `black`，本次验证通过 `uv run` 解决 Python 测试依赖。
- 尚未执行连接真实邮箱 / OAuth / OpenAI 外部链路的集成验证；本次结论基于代码路径审查和离线回归测试。
