from pathlib import Path

from fastapi.routing import APIRoute

from src.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def _read_template(name: str) -> str:
    return (ROOT / "templates" / name).read_text(encoding="utf-8")


def _find_route(app, path: str, method: str) -> APIRoute:
    wanted_method = method.upper()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == path and wanted_method in route.methods:
            return route
    raise AssertionError(f"Route not found: {method} {path}")


def test_templates_include_accessible_labels_for_toolbar_and_bulk_controls():
    expected = {
        "accounts.html": (
            'aria-label="账号状态筛选"',
            'aria-label="邮箱服务筛选"',
            'aria-label="账号标号筛选"',
            'aria-label="搜索账号邮箱"',
            'aria-label="全选账号"',
            'for="auto-quick-refresh-interval"',
            'for="auto-quick-refresh-retry"',
        ),
        "accounts_overview.html": ('aria-label="搜索账号"',),
        "auto_team.html": (
            'aria-label="Team 状态筛选"',
            'aria-label="搜索 Team 邮箱或名称"',
            'aria-label="全选 Team"',
            'aria-label="搜索目标邮箱"',
            'aria-label="搜索邀请账号"',
        ),
        "card_pool.html": (
            'aria-label="供应商筛选"',
            'aria-label="卡池排序方式"',
            'aria-label="搜索兑换码"',
            'aria-label="全选兑换码"',
            'aria-label="每页条数"',
        ),
        "logs.html": (
            'for="cleanup-retention-days"',
            'for="cleanup-max-rows"',
        ),
        "payment.html": (
            'for="currency-display"',
            'for="link-text"',
            'aria-label="搜索绑卡任务邮箱"',
            'aria-label="绑卡任务状态筛选"',
        ),
        "settings.html": (
            'aria-label="全选邮箱服务"',
            'aria-label="导入数据库文件"',
        ),
        "email_services.html": (
            'aria-label="全选自定义邮箱服务"',
            'aria-label="全选 Outlook 账户"',
        ),
    }

    for template_name, snippets in expected.items():
        content = _read_template(template_name)
        for snippet in snippets:
            assert snippet in content, f"{template_name} missing {snippet}"


def test_templates_include_semantic_autocomplete_for_key_inputs():
    expected = {
        "accounts.html": (
            'id="search-input"',
            'autocomplete="search"',
        ),
        "accounts_overview.html": (
            'id="overview-add-email" type="text" placeholder="name@example.com" autocomplete="email"',
            'id="overview-add-password" type="text" placeholder="必填" autocomplete="current-password"',
            'id="overview-import-json"',
            'autocomplete="off"',
        ),
        "auto_team.html": (
            'id="targetEmail"',
            'autocomplete="email"',
            'id="teamImportAccessToken"',
            'id="teamImportEmail"',
            'id="teamImportBatchText"',
        ),
        "card_pool.html": (
            'id="redeem-search"',
            'autocomplete="search"',
            'id="import-codes-input"',
            'id="edit-used-email"',
            'autocomplete="email"',
        ),
        "logs.html": (
            'id="filter-logger" type="text" placeholder="例如 src.web.routes.payment" autocomplete="off"',
            'id="filter-keyword" type="text" placeholder="错误关键字 / task id" autocomplete="off"',
        ),
        "payment.html": (
            'id="workspace-name" value="MyTeam" placeholder="MyTeam" autocomplete="organization"',
            'id="third-party-api-url" type="text" placeholder="https://twilight-river-f148.482091502.workers.dev/" autocomplete="url"',
            'id="vendor-checkout-input" type="text" placeholder="仅作外部卡商参考；官方 Checkout 仍由后端生成" autocomplete="url"',
            'id="billing-paste-text"',
            'id="card-number-input" type="text" autocomplete="cc-number"',
            'id="card-expiry-input" type="text" autocomplete="cc-exp"',
            'id="card-cvc-input" type="text" autocomplete="cc-csc"',
            'id="billing-name-input" type="text" autocomplete="cc-name"',
            'id="billing-country-input" autocomplete="country-name"',
            'id="billing-line1-input" type="text" autocomplete="address-line1"',
            'id="billing-city-input" type="text" autocomplete="address-level2"',
            'id="billing-state-input" type="text" autocomplete="address-level1"',
            'id="billing-postal-input" type="text" autocomplete="postal-code"',
            'id="bind-task-search" class="bind-task-search form-input" type="text" placeholder="搜索邮箱账号..." aria-label="搜索绑卡任务邮箱" autocomplete="search"',
        ),
        "settings.html": (
            'id="dynamic-proxy-api-url" name="api_url" placeholder="http://api.example.com/get_proxy" autocomplete="url"',
            'id="dynamic-proxy-api-key-header" name="api_key_header" value="X-API-Key" autocomplete="off"',
            'id="service-name" name="name" required placeholder="例如：主邮箱服务" autocomplete="off"',
            'id="proxy-batch-import-text"',
            'id="tm-service-url" placeholder="https://tm.example.com" required autocomplete="url"',
            'id="new-api-service-url" placeholder="https://newapi.example.com" required autocomplete="url"',
            'id="cpa-service-proxy-url" placeholder="http://user:pass@host:port 或 socks5://host:port" autocomplete="url"',
            'id="outlook-default-client-id" name="default_client_id" autocomplete="off"',
        ),
        "email_services.html": (
            'id="outlook-import-data"',
            'id="tempmail-api" name="api_url" placeholder="https://tempmail.lol/api" autocomplete="url"',
            'id="yyds-api-key" name="yyds_api_key" placeholder="留空则保持原值不变" autocomplete="off"',
            'id="custom-name" name="name" required placeholder="例如：我的域名邮箱" autocomplete="off"',
            'id="custom-tm-admin-password" name="tm_admin_password" placeholder="x-admin-auth 密码" autocomplete="current-password"',
            'id="custom-imap-email" name="imap_email" placeholder="your@gmail.com" autocomplete="email"',
            'id="custom-cm-admin-email" name="cm_admin_email" placeholder="admin@example.com" autocomplete="email"',
            'id="edit-custom-api-key" name="api_key" placeholder="API Key" autocomplete="off"',
            'id="edit-outlook-email" name="email" required placeholder="example@outlook.com" autocomplete="email"',
            'id="edit-outlook-password" name="password" placeholder="留空则保持原值不变" autocomplete="current-password"',
        ),
        "index.html": (
            'id="auto-registration-proxy" placeholder="留空则沿用现有代理策略" autocomplete="url"',
            'id="schedule-name" maxlength="100" placeholder="例如：每 3 天定时注册并上传" autocomplete="off"',
        ),
    }

    for template_name, snippets in expected.items():
        content = _read_template(template_name)
        for snippet in snippets:
            assert snippet in content, f"{template_name} missing {snippet}"


def test_app_has_favicon_route_and_static_asset():
    app = create_app()
    _find_route(app, "/favicon.ico", "GET")
    assert (ROOT / "static" / "favicon.svg").exists()
