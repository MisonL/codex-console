# 账号批量操作代码审查

## 范围

- 分支：`feature/account-batch-action-tooltips`
- 基础分支：`upstream/main`
- 目标：
  - 修复账号管理页面上损坏的三个批量操作路由
  - 保持按钮在空闲状态下的标签文字稳定
  - 将原生的 `title` 提示替换为显示在按钮下方的悬浮气泡提示

## 验证过程

### 静态检查

命令：

```bash
python3 -m py_compile src/web/routes/accounts.py src/web/routes/payment.py
```

结果：

```text
exit code 0
```

### 运行时检查

独立实例验证：

- URL：`http://127.0.0.1:16667`
- 访问密码：在运行脚本之前，请在本地 Shell 中设置 `REVIEW_LOGIN_PASSWORD` 环境变量。

命令：

```bash
python3 - <<'PY'
import urllib.parse, urllib.request, http.cookiejar, json
import os
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
password = os.environ['REVIEW_LOGIN_PASSWORD']
login_data = urllib.parse.urlencode({'password': password}).encode()
login_req = urllib.request.Request('http://127.0.0.1:16667/login', data=login_data, method='POST')
login_req.add_header('Content-Type', 'application/x-www-form-urlencoded')
login_resp = opener.open(login_req, timeout=10)
print('login_status', login_resp.status)
accounts_resp = opener.open('http://127.0.0.1:16667/accounts', timeout=10)
print('accounts_status', accounts_resp.status)
for path, poll_prefix in [
    ('/api/accounts/batch-refresh/async', '/api/accounts/tasks/'),
    ('/api/accounts/batch-validate/async', '/api/accounts/tasks/'),
    ('/api/payment/accounts/batch-check-subscription/async', '/api/payment/ops/tasks/'),
]:
    req = urllib.request.Request(
        f'http://127.0.0.1:16667{path}',
        data=json.dumps({'ids': [], 'select_all': True}).encode(),
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    resp = opener.open(req, timeout=20)
    payload = json.loads(resp.read().decode() or '{}')
    task_id = payload.get('id') or payload.get('task_id')
    print(path, resp.status, task_id)
    if task_id:
        poll = opener.open(f'http://127.0.0.1:16667{poll_prefix}{task_id}', timeout=20)
        poll_payload = json.loads(poll.read().decode() or '{}')
        print(poll_prefix, poll.status, poll_payload.get('status'))
PY
```

结果：

```text
login_status 200
accounts_status 200
/api/accounts/batch-refresh/async 200 accounts-batch-refresh-f0b2d40566ba
/api/accounts/tasks/ 200 running
/api/accounts/batch-validate/async 200 accounts-batch-validate-1d5627590eb7
/api/accounts/tasks/ 200 completed
/api/payment/accounts/batch-check-subscription/async 200 payment-batch-check-subscription-227ec45d862f
/api/payment/ops/tasks/ 200 completed
```

### UI 界面检查

- 悬停在 `刷新Token` 按钮上，下方会显示自定义的提示气泡
- 悬停在 `验证Token` 按钮上，下方会显示自定义的提示气泡
- 悬停在 `检测订阅` 按钮上，下方会显示自定义的提示气泡
- 当勾选的账号数量发生变化时，这三个按钮在空闲状态下的文字标签依然保持稳定不变

## 结论

- 本分支已成功修复了损坏的批量操作路由
- 悬停提示气泡的行为现已符合所要求的交互模式
- 本次审查无需进行正式的环境部署
