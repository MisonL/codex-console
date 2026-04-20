import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_TEMPLATES = {
    "index.html": "注册设置",
    "accounts.html": "Codex Auth",
    "settings.html": "系统设置",
    "email_services.html": "邮箱服务管理",
    "payment.html": "支付升级",
    "card_pool.html": "卡池",
    "auto_team.html": "Team 管理",
    "selfcheck.html": "系统自检",
    "logs.html": "后台日志监控台",
}
NAV_LINKS = (
    ("/accounts", "账号管理"),
    ("/email-services", "邮箱服务"),
    ("/payment", "支付"),
    ("/card-pool", "卡池"),
    ("/auto-team", "Team 管理"),
    ("/logs", "后台日志"),
    ("/settings", "设置"),
)
LEGACY_NAV_LABELS = (
    'href="/auto-team" class="nav-link">team</a>',
    ">Check</a>",
    ">" + "\u2699\ufe0f" + "</a>",
)


def _read_template(template_name: str) -> str:
    return (ROOT / "templates" / template_name).read_text(encoding="utf-8")


def _assert_nav_link(content: str, path: str, label: str) -> None:
    pattern = (
        rf'href="{re.escape(path)}" class="nav-link(?: active)?">{re.escape(label)}</a>'
    )
    assert re.search(pattern, content), f"Missing nav link {path} -> {label}"


@pytest.mark.parametrize(
    ("template_name", "page_marker"),
    CORE_TEMPLATES.items(),
)
def test_core_templates_include_unified_navigation(
    template_name: str, page_marker: str
) -> None:
    content = _read_template(template_name)

    for path, label in NAV_LINKS:
        _assert_nav_link(content, path, label)

    assert re.search(
        r'href="/selfcheck" class="selfcheck-toggle(?: active)?" title="系统自检">系统自检</a>',
        content,
    )
    assert page_marker in content


@pytest.mark.parametrize("template_name", CORE_TEMPLATES)
def test_core_templates_do_not_use_legacy_navigation_labels(
    template_name: str,
) -> None:
    content = _read_template(template_name)

    for legacy_label in LEGACY_NAV_LABELS:
        assert legacy_label not in content
