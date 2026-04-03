# All Feature Integration Review

## Scope

- Branch: `dev/all-feature-integration`
- Base: `main`
- Date: `2026-04-03`
- Goal:
  - 恢复崩溃前的工作上下文并接手当前集成分支
  - 对集成分支执行静态检查和全量测试
  - 修复验证过程中暴露出的真实问题
  - 统一开发版本到 `2.7.8`，但不创建 release/tag

## Issues Found And Fixed

1. `pyproject.toml` 缺少运行时依赖 `requests`
   - 现象：`uv sync --extra dev` 后测试在收集阶段因 `src/services/cloud_mail.py` 导入 `requests` 失败。
   - 修复：将 `requests>=2.32.5` 补入主依赖，保证 `uv` 环境和项目真实运行依赖一致。

2. `/api/registration/available-services` 对轻量 settings 对象不兼容
   - 现象：接口直接访问 `settings.tempmail_enabled`，测试替身对象缺该属性时抛 `AttributeError`。
   - 修复：改为带默认值的属性读取，兼容轻量配置对象，同时保持正式配置行为不变。

3. `TempMailService` 未正确补取邮件详情正文
   - 现象：列表接口仅返回摘要时，验证码提取逻辑在补拉详情前就提前过滤邮件，导致详情接口无法参与 OTP 识别。
   - 修复：对无正文的候选邮件先补拉详情，再执行 OpenAI OTP 邮件判断和验证码提取。

## Verification

### Static Check

```bash
python3 -m py_compile src/web/routes/accounts.py src/web/routes/payment.py src/web/routes/registration.py src/core/openai/codex_auth_workbench.py src/core/register.py src/core/anyauto/chatgpt_client.py src/core/anyauto/register_flow.py src/config/settings.py src/config/constants.py
node --check static/js/accounts.js
node --check static/js/app.js
node --check static/js/email_services.js
node --check static/js/settings.js
```

Result:

```text
exit code 0
```

### Targeted Regression

```bash
uv sync --extra dev
uv run python -m pytest -q tests/test_codex_auth_workbench.py tests/test_security_and_task_routes.py tests/test_registration_wait_strategy.py tests/test_registration_password_hardening.py tests/test_settings_registration_auto_fields.py tests/test_registration_engine.py
uv run python -m pytest -q tests/test_email_service_duckmail_routes.py tests/test_temp_mail_service.py
```

Result:

```text
32 passed in 5.97s
11 passed in 7.09s
```

### Full Test Suite

```bash
uv run python -m pytest -q
```

Result:

```text
82 passed in 17.15s
```

## Conclusion

- 当前集成分支在本地静态检查和全量测试下已通过。
- 本次只更新开发版本到 `2.7.8`，未修改历史 release 文档、tag 元数据或发布产物。
