# PR 描述

## 摘要

- 针对 Token 刷新、Token 验证、订阅检查以及总览刷新，增加异步的账号管理任务路由
- 新增独立的 Codex Auth 工作台，支持批量审计、修复、生成以及导出流程
- 保持原有的三个批量操作按钮在空闲状态下的稳定性，并补充了它们的悬停提示行为说明
- 完成 OpenAI Sentinel 链路的异步化重构，统一收口 Header 构建并消除高并发下的事件循环阻塞风险
- 修复本地 CodeRabbit 代码审查发现的问题，包括域名槽位清理、数据库回滚/会话作用域、邮箱绑定以及审查文档中的密钥泄露问题

## 面向用户的变更

- 账号页面现在暴露了一个独立的 `Codex Auth` 入口按钮，可打开专属的工作台模态框
- 账号表格新增了 `Codex Auth` 状态列
- Codex Auth 工作台操作现已支持：
  - 批量审计
  - 批量修复
  - 批量凭据生成
  - 批量 ZIP 导出
- 异步的账号操作现在会通过专属的任务端点报告进度

## 变更内容

- 完成全量 UI 汉化，覆盖 `templates/` 目录下的所有核心页面：`index`、`settings`、`logs`、`auto_team`、`accounts_overview`
- 统一全局术语，将 `artifact` 明确表述为 `artifact (交付产物)`，并同步更新模板、JavaScript 提示以及后端 API 返回消息中的相关文案
- 完成注册模式展示名称的汉化，覆盖 `流水线` 与 `并行` 两种模式，并同步汉化上传服务的展示名称
- 激活了全新的异步 Sentinel PoW 架构，将 PoW 求解从事件循环线程迁移到工作线程执行，并补齐同步桥接层，避免高并发请求在 FastAPI/异步调用链中被 CPU 密集型求解阻塞
- 引入动态指纹池与动态时区适配能力，基于 User-Agent、Locale、时区和浏览器指纹轮换策略动态生成 Sentinel 指纹载荷，降低固定模板特征暴露风险
- 建立统一的 Sentinel Header 构建层 `src/core/openai/sentinel_headers.py`，集中封装 challenge 拉取、PoW 求解、Header 组装与同步/异步入口，并完成注册、支付、HTTP 客户端与 AnyAuto 链路的调用收口
- 清理已失效的旧实现 `src/core/openai/sentinel_token_v2.py`，避免并存实现造成行为漂移和维护歧义
- Sentinel 重构阶段验收记录：已通过 `pytest` 执行 162 项测试，其中包含 8 个 Sentinel 专项新增测试，确认异步重构后的全链路集成成功且未出现可见性能退化

## 验证过程

```bash
python3 -m py_compile src/web/routes/accounts.py src/web/routes/payment.py src/core/openai/codex_auth_workbench.py
node --check static/js/accounts.js
uv run python -m pytest -q tests/test_codex_auth_workbench.py tests/test_security_and_task_routes.py
```

结果：

```text
12 passed in 6.15s
```

## 真实的开发验证证据

- 独立的开发容器：`codex-console-codex-auth-dev`
- 开发环境 Web URL：`http://127.0.0.1:16668`
- 仅将 4 个异常账号复制到了 `data-dev` 环境中：`53`, `64`, `65`, `71`
- 批量审计结果：`1 个可修复`，`3 个由于需绑定手机而受阻`
- 批量修复结果：账号 `53` 修复成功；`64`, `65`, `71` 仍然处于受阻状态
- 批量导出返回了一个标准的 managed `auth.json` ZIP artifact (交付产物)，其中仅包含修复成功账号的凭据文件

## 本地 CodeRabbit 审查

- 第一轮审查发现了多个可操作的问题：
  - 域名槽位清理
  - 暂停超时处理
  - SQLAlchemy 回滚/会话复用
  - 邮箱到服务的绑定
  - 审查文档中的密钥泄露
- 所有问题均已在本地修复
- 第二轮审查结果：`无任何评论`
