# CR-ISSUES-CURRENT-STATE-2026-04-20

## 范围

- 当前主线代码基线
- 正式 Docker 部署口径
- AnyAuto / Sentinel 注册链路当前实现
- 当前已知边界与剩余风险

## 主线状态

- 当前主分支：`main`
- 当前主线提交：`2f22491` `对齐 anyauto 核心注册链路`
- 最近主线相关提交：
  - `6a4d66e` `Install sentinel vm node deps in image`
  - `79461d9` `Harden production docker deployment`
  - `66cc359` `Merge branch 'dev/local-integration'`

说明：

- 当前仓库已不再以 `dev/local-integration` 作为运行主线，实际工作基线已经收口到 `main`。
- 本文档记录的是 `2026-04-20` 当天的阶段性真相；若后续代码继续演进，应以 `git log` 和代码事实为准。

## 正式 Docker 部署口径

当前正式部署使用根目录 `docker-compose.yml`，不是 `docker-compose.local-integration.yml`。

- 服务名：`webui`
- 容器名：`codex-console-production`
- 对外端口：
  - Web UI：`16670 -> 1455`
  - noVNC：`6080 -> 6080`
- 持久化挂载：
  - `./data:/app/data`
  - `./logs:/app/logs`
- 必填环境变量：
  - `WEBUI_ACCESS_PASSWORD`

推荐重建命令：

```bash
WEBUI_ACCESS_PASSWORD=your_secure_password \
docker compose --project-directory "$(pwd -P)" -f docker-compose.yml up -d --build
```

## 2026-04-20 正式服务复核结果

部署时间点：

- 容器启动时间：`2026-04-20T09:59:41Z`
- 运行镜像：`sha256:1061ae77d1119b1b689a2e533be558bce29be5660b1ca7fb42f313890ce0fa9c`

验证结果：

- `docker compose ps`：`codex-console-production` 为 `Up`
- `GET http://127.0.0.1:16670/`：返回 `302 Found`，重定向到 `/login?next=/`
- `GET http://127.0.0.1:6080/vnc.html`：返回 `200 OK`
- 容器日志显示 Web UI、调度器、自检调度器均已正常启动

## 注册链路当前实现

### 已确认修复

1. 本地 Chrome / CDP 基础设施问题已处理
   - 已解决的历史问题包括：
     - 浏览器二进制定位失败
     - CDP readiness 检测不稳定
   - 对应文件：
     - `src/core/openai/browser_bind.py`
     - `src/core/openai/sentinel_browser.py`
     - `tests/test_browser_bind_cdp.py`

2. AnyAuto 写操作 Sentinel 策略已对齐到浏览器优先
   - 当前 `register_user()`、`create_account()`、`OAuth about_you create_account` 已切换为：
     - 浏览器 Sentinel / Turnstile 优先
     - 本地 PoW 兜底
   - 对应入口：
     - `src/core/anyauto/chatgpt_client.py`
     - `src/core/anyauto/oauth_client.py`

3. 旧失败语义已保持兼容
   - 对 `ChatGPTClient` 两个写接口，若浏览器 Sentinel 和本地 PoW 都失败，仍保持历史 `"{}"` header 口径，不额外引入新的静默行为变化。

### 当前离线验证证据

执行命令：

```bash
uv run --extra dev python -m pytest -q \
  tests/test_anyauto_auth_flow.py \
  tests/test_registration_password_hardening.py \
  tests/test_openai_sentinel.py \
  tests/test_openai_sentinel_headers.py \
  tests/test_browser_bind_cdp.py \
  tests/test_registration_engine.py \
  tests/test_chatgpt_registration_mode.py \
  tests/test_auto_registration_merge.py

uv run --extra dev python -m py_compile \
  src/core/anyauto/chatgpt_client.py \
  src/core/anyauto/oauth_client.py \
  tests/test_anyauto_auth_flow.py \
  tests/test_registration_password_hardening.py
```

结果：

- `pytest`：`58 passed`
- `py_compile`：退出码 `0`

## 当前已知边界

当前剩余的核心问题已经不在本地 Docker / Chrome / CDP 存活层，而在真实站点风控层。

最新已知真实失败点：

- `create-account/password` 阶段直接返回 `400`
- 错误语义已归一为“当前出口 IP / 设备指纹 / 会话环境很可能触发风控”

这意味着：

- 本地浏览器可启动、CDP 可连、Sentinel 可生成，并不等于真实注册一定成功
- 离线测试全绿只能证明当前代码路径自洽，不能替代真实外部链路复验

## 审查补充说明

- 本地静态复核已完成，并清除了 `register_user()` 中一段无效的旧 Sentinel 生成死代码
- 第二次 `CodeRabbit` 外部审查在 `2026-04-20` 当天命中服务限流，未形成新的审查结论

## 结论

截至 `2026-04-20`：

- 主线代码基线已经统一到 `main`
- 正式服务部署口径已经统一到根目录 `docker-compose.yml`
- AnyAuto 核心写操作 Sentinel 策略已对齐为“浏览器优先、本地 PoW 兜底”
- 当前主要剩余风险在 OpenAI 真实风控环境，而不是本地基础设施故障
