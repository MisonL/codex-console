"""
Any-auto-register 无 RT 方案。
仅复用注册会话获取 Access Token / Session Token，不要求 Refresh Token。
"""

from __future__ import annotations

import secrets
import string
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from .chatgpt_client import ChatGPTClient
from .utils import decode_jwt_payload, generate_random_birthday, generate_random_name
from ...config.constants import DEFAULT_PASSWORD_LENGTH, PASSWORD_CHARSET, PASSWORD_SPECIAL_CHARSET


class EmailServiceAdapter:
    """将 codex-console 邮箱服务适配成 any-auto-register 预期接口。"""

    def __init__(self, email_service, email: str, email_id: Optional[str], log_fn: Callable[[str], None]):
        self.es = email_service
        self.email = email
        self.email_id = email_id
        self.log_fn = log_fn or (lambda _msg: None)
        self._used_codes: set[str] = set()

    def wait_for_verification_code(self, email, timeout=60, otp_sent_at=None, exclude_codes=None):
        exclude = set(exclude_codes or [])
        exclude.update(self._used_codes)
        deadline = time.time() + max(1, int(timeout))
        sent_at = otp_sent_at or time.time()

        while time.time() < deadline:
            remaining = max(1, int(deadline - time.time()))
            code = self.es.get_verification_code(
                email=email,
                email_id=self.email_id,
                timeout=remaining,
                otp_sent_at=sent_at,
            )
            if not code:
                return None
            if code in exclude:
                exclude.add(code)
                continue
            self._used_codes.add(code)
            self.log_fn(f"成功获取验证码: {code}")
            return code
        return None


class AccessTokenOnlyRegistrationEngine:
    def __init__(
        self,
        email_service,
        proxy_url: Optional[str] = None,
        callback_logger: Optional[Callable[[str], None]] = None,
        max_retries: int = 3,
        browser_mode: str = "protocol",
        extra_config: Optional[Dict[str, Any]] = None,
    ):
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.callback_logger = callback_logger or (lambda _msg: None)
        self.max_retries = max(1, int(max_retries or 1))
        self.browser_mode = browser_mode or "protocol"
        self.extra_config = dict(extra_config or {})

        self.email: Optional[str] = None
        self.inbox_email: Optional[str] = None
        self.email_info: Optional[Dict[str, Any]] = None
        self.password: Optional[str] = None
        self.session = None
        self.device_id: Optional[str] = None

    def _log(self, message: str, level: str = "info"):
        if self.callback_logger:
            try:
                self.callback_logger(message, level=level)
            except TypeError:
                self.callback_logger(message)

    @staticmethod
    def _build_password(length: int) -> str:
        length = max(8, int(length or DEFAULT_PASSWORD_LENGTH))
        password_chars = [
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.digits),
            secrets.choice(PASSWORD_SPECIAL_CHARSET),
        ]
        password_chars.extend(
            secrets.choice(PASSWORD_CHARSET) for _ in range(length - len(password_chars))
        )
        secrets.SystemRandom().shuffle(password_chars)
        return "".join(password_chars)

    @staticmethod
    def _should_retry(message: str) -> bool:
        text = str(message or "").lower()
        non_retryable_markers = [
            "account_deactivated",
            "openai 未继续发送 otp",
            "已放弃本轮 oauth 验证",
        ]
        if any(marker in text for marker in non_retryable_markers):
            return False
        retriable_markers = [
            "tls",
            "ssl",
            "curl: (35)",
            "预授权被拦截",
            "authorize",
            "registration_disallowed",
            "http 400",
            "创建账号失败",
            "未获取到 authorization code",
            "consent",
            "workspace",
            "organization",
            "otp",
            "验证码",
            "session",
            "accesstoken",
            "next-auth",
        ]
        return any(marker in text for marker in retriable_markers)

    @staticmethod
    def _extract_account_id_from_token(token: str) -> str:
        payload = decode_jwt_payload(token)
        if not isinstance(payload, dict):
            return ""
        auth_claims = payload.get("https://api.openai.com/auth") or {}
        for key in ("chatgpt_account_id", "account_id", "workspace_id"):
            value = str(auth_claims.get(key) or payload.get(key) or "").strip()
            if value:
                return value
        return ""

    def run(self):
        last_error = ""
        password_len = int(self.extra_config.get("default_password_length") or DEFAULT_PASSWORD_LENGTH)

        for attempt in range(self.max_retries):
            try:
                if attempt == 0:
                    self._log("=" * 60)
                    self._log("开始注册流程 V2 (无 RT / Session 复用方案)")
                    self._log(f"请求模式: {self.browser_mode}")
                    self._log("=" * 60)
                else:
                    self._log(f"整流程重试 {attempt + 1}/{self.max_retries} ...")
                    time.sleep(1)

                self.email_info = self.email_service.create_email()
                raw_email = str((self.email_info or {}).get("email") or "").strip()
                if not raw_email:
                    last_error = "创建邮箱失败"
                    return {"success": False, "error_message": last_error}

                normalized_email = raw_email.lower()
                self.inbox_email = raw_email
                self.email = normalized_email
                try:
                    self.email_info["email"] = normalized_email
                except Exception:
                    pass

                if raw_email != normalized_email:
                    self._log(f"邮箱规范化: {raw_email} -> {normalized_email}")

                self.password = self.password or self._build_password(password_len)
                first_name, last_name = generate_random_name()
                birthdate = generate_random_birthday()
                self._log(f"邮箱: {normalized_email}, 密码: {self.password}")
                self._log(f"注册信息: {first_name} {last_name}, 生日: {birthdate}")

                email_id = (self.email_info or {}).get("service_id")
                mailbox = EmailServiceAdapter(self.email_service, normalized_email, email_id, self._log)

                client = ChatGPTClient(
                    proxy=self.proxy_url,
                    verbose=False,
                    browser_mode=self.browser_mode,
                )
                client._log = self._log

                self._log("步骤 1/2: 执行注册状态机...")
                success, msg = client.register_complete_flow(
                    normalized_email,
                    self.password,
                    first_name,
                    last_name,
                    birthdate,
                    mailbox,
                )
                if not success:
                    last_error = f"注册流失败: {msg}"
                    if attempt < self.max_retries - 1 and self._should_retry(msg):
                        self._log(f"注册流失败，准备整流程重试: {msg}")
                        continue
                    return {"success": False, "error_message": last_error}

                self.session = client.session
                self.device_id = client.device_id

                self._log("步骤 2/2: 复用注册会话，直接获取 ChatGPT Session / AccessToken...")
                session_ok, session_result = client.reuse_session_and_get_tokens()
                if session_ok:
                    account_id = str(session_result.get("account_id", "") or "").strip()
                    if not account_id:
                        account_id = str(session_result.get("workspace_id", "") or "").strip()
                    if not account_id:
                        account_id = self._extract_account_id_from_token(session_result.get("access_token", ""))
                    workspace_id = str(session_result.get("workspace_id", "") or "").strip() or account_id

                    return {
                        "success": True,
                        "access_token": session_result.get("access_token", ""),
                        "session_token": session_result.get("session_token", ""),
                        "account_id": account_id,
                        "workspace_id": workspace_id,
                        "metadata": {
                            "auth_provider": session_result.get("auth_provider", ""),
                            "expires": session_result.get("expires", ""),
                            "user_id": session_result.get("user_id", ""),
                            "user": session_result.get("user") or {},
                            "account": session_result.get("account") or {},
                        },
                    }

                last_error = f"注册成功，但复用会话获取 AccessToken 失败: {session_result}"
                if attempt < self.max_retries - 1 and self._should_retry(last_error):
                    self._log(f"{last_error}，准备整流程重试")
                    continue
                return {"success": False, "error_message": last_error}

            except Exception as attempt_error:
                last_error = str(attempt_error)
                if attempt < self.max_retries - 1 and self._should_retry(last_error):
                    self._log(f"本轮出现异常，准备整流程重试: {last_error}")
                    continue
                return {"success": False, "error_message": last_error}

        return {"success": False, "error_message": last_error or "注册失败"}
