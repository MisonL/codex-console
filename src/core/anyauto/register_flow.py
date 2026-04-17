"""
Any-auto-register 有 RT 方案。
以注册态回调落地 + PKCE 换码为主，目标产出 Access Token + Refresh Token。
"""

from __future__ import annotations

import secrets
import string
import time
import base64
import json
from datetime import datetime
from typing import Optional, Callable, Dict, Any

from .chatgpt_client import ChatGPTClient
from .oauth_client import OAuthClient
from .utils import generate_random_name, generate_random_birthday, decode_jwt_payload
from ...config.constants import PASSWORD_CHARSET, PASSWORD_SPECIAL_CHARSET, DEFAULT_PASSWORD_LENGTH
from ...config.settings import get_settings


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


class AnyAutoRegistrationEngine:
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
        self._last_passwordless_error: str = ""

    def _log(self, message: str, level: str = "info"):
        if self.callback_logger:
            # 兼容旧的回调接口
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
        password_chars.extend(secrets.choice(PASSWORD_CHARSET) for _ in range(length - len(password_chars)))
        secrets.SystemRandom().shuffle(password_chars)
        return "".join(password_chars)

    @staticmethod
    def _should_retry(message: str) -> bool:
        text = str(message or "").lower()
        non_retryable_markers = [
            "account_deactivated",
            "registration_disallowed",
            "cannot create your account with the given information",
            "不允许继续创建账号",
            "当前出口 ip / 设备指纹 / 会话环境很可能触发风控",
            "create-account/password 阶段",
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
        return any(marker.lower() in text for marker in retriable_markers)

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

    @staticmethod
    def _is_phone_required_error(message: str) -> bool:
        text = str(message or "").lower()
        return any(
            marker in text
            for marker in (
                "add_phone",
                "add-phone",
                "phone",
                "phone required",
                "phone verification",
                "手机号",
            )
        )

    @staticmethod
    def _is_account_deactivated_error(message: str) -> bool:
        text = str(message or "").strip().lower()
        return any(
            marker in text
            for marker in (
                "account_deactivated",
                "access deactivated",
                "account deactivated",
                "deactivated account",
            )
        )

    @staticmethod
    def _decode_cookie_json_value(value: str) -> Dict[str, Any]:
        raw_value = str(value or "").strip()
        if not raw_value:
            return {}

        candidates = [raw_value]
        if "." in raw_value:
            candidates.insert(0, raw_value.split(".", 1)[0])

        for candidate in candidates:
            padded = candidate + "=" * (-len(candidate) % 4)
            for decoder in (base64.urlsafe_b64decode, base64.b64decode):
                try:
                    decoded = decoder(padded).decode("utf-8")
                    parsed = json.loads(decoded)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return {}

    @classmethod
    def _extract_workspace_id_from_session_payload(cls, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        workspace_id = str(
            payload.get("workspace_id")
            or payload.get("default_workspace_id")
            or ((payload.get("workspace") or {}).get("id") if isinstance(payload.get("workspace"), dict) else "")
            or ""
        ).strip()
        if workspace_id:
            return workspace_id
        workspaces = payload.get("workspaces") or []
        if isinstance(workspaces, list) and workspaces:
            return str((workspaces[0] or {}).get("id") or "").strip()
        return ""

    def _capture_partial_auth_result(
        self,
        chatgpt_client: ChatGPTClient,
        oauth_client: Optional[OAuthClient] = None,
    ) -> Dict[str, Any]:
        partial: Dict[str, Any] = {
            "access_token": "",
            "refresh_token": str(getattr(chatgpt_client, "refresh_token", "") or "").strip(),
            "id_token": "",
            "session_token": "",
            "account_id": "",
            "workspace_id": "",
            "metadata": {},
        }

        get_session_token = getattr(chatgpt_client, "get_next_auth_session_token", None)
        if callable(get_session_token):
            try:
                partial["session_token"] = str(get_session_token() or "").strip()
            except Exception:
                pass

        session_payload: Dict[str, Any] = {}
        if oauth_client is not None:
            decode_session_cookie = getattr(oauth_client, "_decode_oauth_session_cookie", None)
            if callable(decode_session_cookie):
                try:
                    decoded_payload = decode_session_cookie()
                    if isinstance(decoded_payload, dict):
                        session_payload = decoded_payload
                except Exception:
                    session_payload = {}

        if not session_payload:
            try:
                auth_cookie = str(chatgpt_client.session.cookies.get("oai-client-auth-session") or "").strip()
            except Exception:
                auth_cookie = ""
            if auth_cookie:
                session_payload = self._decode_cookie_json_value(auth_cookie)

        partial["workspace_id"] = self._extract_workspace_id_from_session_payload(session_payload)

        try:
            session_ok, session_result = chatgpt_client.fetch_chatgpt_session()
        except Exception as exc:
            self._log(f"add_phone 场景补抓 auth/session 失败: {exc}", level="warning")
            session_ok = False
            session_result = str(exc)

        if session_ok and isinstance(session_result, dict):
            access_token = str(session_result.get("accessToken") or "").strip()
            session_token = str(session_result.get("sessionToken") or partial["session_token"] or "").strip()
            user = session_result.get("user") or {}
            account = session_result.get("account") or {}
            auth_payload = decode_jwt_payload(access_token).get("https://api.openai.com/auth") or {}
            account_id = str(
                account.get("id")
                or auth_payload.get("chatgpt_account_id")
                or auth_payload.get("account_id")
                or ""
            ).strip()
            user_id = str(
                user.get("id")
                or auth_payload.get("chatgpt_user_id")
                or auth_payload.get("user_id")
                or ""
            ).strip()

            partial["access_token"] = access_token
            partial["session_token"] = session_token
            partial["account_id"] = account_id
            partial["workspace_id"] = partial["workspace_id"] or account_id
            partial["metadata"] = {
                "user_id": user_id,
                "user": user,
                "account": account,
                "auth_provider": session_result.get("authProvider"),
                "expires": session_result.get("expires"),
                "raw_session": session_result,
            }
        elif session_result:
            partial["metadata"] = {
                "partial_auth_error": str(session_result),
            }

        if not partial["account_id"] and partial["access_token"]:
            partial["account_id"] = self._extract_account_id_from_token(partial["access_token"])
        if not partial["workspace_id"]:
            partial["workspace_id"] = partial["account_id"]

        return partial

    @staticmethod
    def _dump_cookie_text(session) -> str:
        if session is None:
            return ""

        cookie_map: Dict[str, str] = {}
        ordered_keys: list[str] = []

        def _push(name, value):
            key = str(name or "").strip()
            val = str(value or "").strip()
            if not key:
                return
            if key not in cookie_map:
                ordered_keys.append(key)
            previous = str(cookie_map.get(key) or "").strip()
            if val or not previous:
                cookie_map[key] = val

        try:
            for key, value in session.cookies.items():
                _push(key, value)
        except Exception:
            pass

        try:
            jar = getattr(session.cookies, "jar", None)
            if jar is not None:
                for cookie in jar:
                    _push(getattr(cookie, "name", ""), getattr(cookie, "value", ""))
        except Exception:
            pass

        return "; ".join(f"{key}={cookie_map.get(key, '')}" for key in ordered_keys if key)

    @staticmethod
    def _build_continue_url(*states) -> str:
        for state in states:
            if state is None:
                continue
            continue_url = str(getattr(state, "continue_url", "") or "").strip()
            if continue_url:
                return continue_url
            current_url = str(getattr(state, "current_url", "") or "").strip()
            if current_url:
                return current_url
        return ""

    def _build_repair_metadata(
        self,
        *,
        chatgpt_client: Optional[ChatGPTClient] = None,
        oauth_client: Optional[OAuthClient] = None,
        state: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session = None
        if oauth_client is not None and getattr(oauth_client, "session", None) is not None:
            session = oauth_client.session
        elif chatgpt_client is not None:
            session = getattr(chatgpt_client, "session", None)

        cookies = self._dump_cookie_text(session)
        continue_url = self._build_continue_url(
            getattr(oauth_client, "last_state", None) if oauth_client is not None else None,
            getattr(chatgpt_client, "last_registration_state", None) if chatgpt_client is not None else None,
        )

        metadata = {
            "state": str(state or "").strip(),
            "continue_url": continue_url,
            "cookies": cookies,
        }
        if extra:
            metadata.update(dict(extra))
        return metadata

    def _build_salvage_result(
        self,
        *,
        state: str,
        chatgpt_client: ChatGPTClient,
        oauth_client: Optional[OAuthClient] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error_message: str = "",
    ) -> Dict[str, Any]:
        partial_auth = self._capture_partial_auth_result(chatgpt_client, oauth_client)
        merged_metadata = dict(partial_auth.get("metadata") or {})
        if metadata:
            merged_metadata.update(dict(metadata))
        merged_metadata["repair_metadata"] = self._build_repair_metadata(
            chatgpt_client=chatgpt_client,
            oauth_client=oauth_client,
            state=state,
            extra=merged_metadata.get("repair_metadata"),
        )
        merged_metadata["result_state"] = state
        if error_message:
            merged_metadata["oauth_error"] = str(error_message)

        return {
            "success": True,
            "state": state,
            "access_token": partial_auth.get("access_token", ""),
            "refresh_token": partial_auth.get("refresh_token", ""),
            "id_token": partial_auth.get("id_token", ""),
            "session_token": partial_auth.get("session_token", ""),
            "account_id": partial_auth.get("account_id", ""),
            "workspace_id": partial_auth.get("workspace_id", ""),
            "metadata": merged_metadata,
        }

    def _passwordless_oauth_reauth(
        self,
        chatgpt_client: ChatGPTClient,
        email: str,
        skymail_adapter: EmailServiceAdapter,
        oauth_config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        self._log("检测到 add_phone，尝试 passwordless OTP 登录补全 workspace...")
        self._last_passwordless_error = ""
        oauth_client = OAuthClient(
            config=oauth_config,
            proxy=self.proxy_url,
            verbose=False,
            browser_mode=self.browser_mode,
        )
        oauth_client._log = self._log
        oauth_client.apply_auth_context(chatgpt_client.export_auth_context())

        tokens = oauth_client.login_passwordless_and_get_tokens(
            email,
            chatgpt_client.device_id,
            chatgpt_client.ua,
            chatgpt_client.sec_ch_ua,
            chatgpt_client.impersonate,
            skymail_adapter,
        )
        if tokens and tokens.get("access_token"):
            partial_auth = self._capture_partial_auth_result(chatgpt_client, oauth_client)
            account_id = (
                partial_auth.get("account_id")
                or self._extract_account_id_from_token(tokens.get("access_token", ""))
            )
            workspace_id = partial_auth.get("workspace_id") or account_id
            return {
                "access_token": tokens.get("access_token", ""),
                "refresh_token": tokens.get("refresh_token", ""),
                "id_token": tokens.get("id_token", ""),
                "session_token": partial_auth.get("session_token", ""),
                "account_id": account_id,
                "workspace_id": workspace_id,
                "metadata": {
                    **(partial_auth.get("metadata") or {}),
                    "repair_metadata": self._build_repair_metadata(
                        chatgpt_client=chatgpt_client,
                        oauth_client=oauth_client,
                        state="active",
                    ),
                },
                "session": oauth_client.session,
            }

        if oauth_client.last_error:
            self._last_passwordless_error = str(oauth_client.last_error or "").strip()
            self._log(f"Passwordless OAuth 失败: {oauth_client.last_error}")
        return None

    def run(self):
        """
        执行 any-auto-register 风格注册流程。
        返回 dict：包含 result(RegistrationResult 填充所需字段) + 额外上下文。
        """
        last_error = ""
        settings = get_settings()
        password_len = int(getattr(settings, "registration_default_password_length", DEFAULT_PASSWORD_LENGTH) or DEFAULT_PASSWORD_LENGTH)

        oauth_config = dict(self.extra_config or {})
        if not oauth_config:
            oauth_config = {
                "oauth_issuer": str(getattr(settings, "openai_auth_url", "") or "https://auth.openai.com"),
                "oauth_client_id": str(getattr(settings, "openai_client_id", "") or "app_EMoamEEZ73f0CkXaXp7hrann"),
                "oauth_redirect_uri": str(getattr(settings, "openai_redirect_uri", "") or "http://localhost:1455/auth/callback"),
            }

        for attempt in range(self.max_retries):
            try:
                if attempt == 0:
                    self._log("=" * 60)
                    self._log("开始注册流程 V2 (有 RT / 新 PR 链路)")
                    self._log(f"请求模式: {self.browser_mode}")
                    self._log("=" * 60)
                else:
                    self._log(f"整流程重试 {attempt + 1}/{self.max_retries} ...")
                    time.sleep(1)

                # 1. 创建邮箱
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

                # 2. 生成密码 & 用户信息
                self.password = self.password or self._build_password(password_len)
                first_name, last_name = generate_random_name()
                birthdate = generate_random_birthday()
                self._log(f"邮箱: {normalized_email}, 密码: {self.password}")
                self._log(f"注册信息: {first_name} {last_name}, 生日: {birthdate}")

                # 3. 邮箱适配器
                email_id = (self.email_info or {}).get("service_id")
                skymail_adapter = EmailServiceAdapter(self.email_service, normalized_email, email_id, self._log)

                # 4. 注册状态机
                chatgpt_client = ChatGPTClient(
                    proxy=self.proxy_url,
                    verbose=False,
                    browser_mode=self.browser_mode,
                )
                chatgpt_client._log = self._log

                self._log("步骤 1/2: 执行注册状态机...")
                success, msg = chatgpt_client.register_complete_flow(
                    normalized_email, self.password, first_name, last_name, birthdate, skymail_adapter
                )
                if not success:
                    last_error = f"注册流失败: {msg}"
                    if attempt < self.max_retries - 1 and self._should_retry(msg):
                        self._log(f"注册流失败，准备整流程重试: {msg}")
                        continue
                    return {"success": False, "error_message": last_error}

                add_phone_required = "add_phone" in str(msg or "").lower()
                interrupted_for_reauth = "otp_verified_interrupted" in str(msg or "").lower()
                
                try:
                    state = getattr(chatgpt_client, "last_registration_state", None)
                    if state:
                        target = f"{getattr(state, 'continue_url', '')} {getattr(state, 'current_url', '')}".lower()
                        if "add-phone" in target or "add_phone" in str(getattr(state, "page_type", "")).lower():
                            add_phone_required = True
                except Exception:
                    pass

                # 保存会话与设备
                self.session = chatgpt_client.session
                self.device_id = chatgpt_client.device_id
                salvage_state = "pending_phone" if add_phone_required else "pending_about_you"

                if add_phone_required or interrupted_for_reauth:
                    if interrupted_for_reauth:
                        self._log("捕获到注册中断信号，立即启动 Passwordless OAuth 接力流程...")
                    else:
                        self._log("检测到账号需要手机号验证，尝试通过 Passwordless OAuth 补全流程...")
                        
                    pwdless = self._passwordless_oauth_reauth(
                        chatgpt_client,
                        normalized_email,
                        skymail_adapter,
                        oauth_config,
                    )
                    if pwdless and pwdless.get("access_token") and pwdless.get("refresh_token"):
                        self.session = pwdless.get("session") or self.session
                        return {
                            "success": True,
                            "state": "active",
                            "access_token": pwdless.get("access_token", ""),
                            "refresh_token": pwdless.get("refresh_token", ""),
                            "id_token": pwdless.get("id_token", ""),
                            "session_token": pwdless.get("session_token", ""),
                            "account_id": pwdless.get("account_id", ""),
                            "workspace_id": pwdless.get("workspace_id", ""),
                            "metadata": pwdless.get("metadata") or {},
                        }
                    if self._is_account_deactivated_error(getattr(self, "_last_passwordless_error", "")):
                        return {"success": False, "error_message": "account_deactivated"}

                # 5. 复用 session 取 token
                self._log("步骤 2/2: 复用注册会话，优先走 callback 落地 + PKCE 换码...")
                session_ok, session_result = chatgpt_client.reuse_session_and_get_tokens()
                if session_ok:
                    resolved_refresh_token = str(
                        chatgpt_client.refresh_token or session_result.get("refresh_token", "") or ""
                    ).strip()
                    if resolved_refresh_token:
                        self._log(f"已持有持久化 RT: {resolved_refresh_token[:20]}...")
                        account_id = str(session_result.get("account_id", "") or "").strip()
                        if not account_id:
                            account_id = str(session_result.get("workspace_id", "") or "").strip()
                        if not account_id:
                            account_id = self._extract_account_id_from_token(session_result.get("access_token", ""))
                        workspace_id = str(session_result.get("workspace_id", "") or "").strip() or account_id
                        return {
                            "success": True,
                            "state": "active",
                            "access_token": session_result.get("access_token", ""),
                            "refresh_token": resolved_refresh_token,
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

                    self._log("复用会话已拿到 Access Token，但未产出 RT，转入显式 OAuth 补全流程...")
                elif self._is_account_deactivated_error(session_result):
                    return {"success": False, "error_message": "account_deactivated"}

                preserved_refresh_token = str(getattr(chatgpt_client, "refresh_token", "") or "").strip()
                if preserved_refresh_token:
                    self._log("复用会话未完成 session 落地，但已捕获 RT，停止重 OAuth salvage 并保留 RT")
                    return {
                        "success": True,
                        "state": "active",
                        "access_token": str((session_result or {}).get("access_token") or ""),
                        "refresh_token": preserved_refresh_token,
                        "id_token": str((session_result or {}).get("id_token") or ""),
                        "session_token": str((session_result or {}).get("session_token") or ""),
                        "account_id": str((session_result or {}).get("account_id") or ""),
                        "workspace_id": str((session_result or {}).get("workspace_id") or ""),
                        "metadata": {
                            "auth_provider": str((session_result or {}).get("auth_provider") or "captured_refresh_token"),
                            "token_pending": True,
                            "salvage_skipped": True,
                            "salvage_skip_reason": "refresh_token_already_captured",
                            "session_error": str(session_result or ""),
                        },
                    }

                # 6. OAuth 回退
                self._log(f"复用会话未完成 RT 产出，回退到 OAuth 登录补全流程: {session_result}")
                tokens = None
                oauth_client = None
                for oauth_attempt in range(2):
                    if oauth_attempt > 0:
                        self._log(f"同账号 OAuth 重试 {oauth_attempt + 1}/2 ...")
                        time.sleep(1)

                    oauth_client = OAuthClient(
                        config=oauth_config,
                        proxy=self.proxy_url,
                        verbose=False,
                        browser_mode=self.browser_mode,
                    )
                    oauth_client._log = self._log
                    oauth_client.apply_auth_context(chatgpt_client.export_auth_context())

                    tokens = oauth_client.login_and_get_tokens(
                        normalized_email,
                        self.password,
                        chatgpt_client.device_id,
                        chatgpt_client.ua,
                        chatgpt_client.sec_ch_ua,
                        chatgpt_client.impersonate,
                        skymail_adapter,
                    )
                    if tokens and tokens.get("access_token") and tokens.get("refresh_token"):
                        break

                    if self._is_account_deactivated_error(oauth_client.last_error):
                        return {"success": False, "error_message": "account_deactivated"}
                    if oauth_client.last_error and "add_phone" in oauth_client.last_error:
                        break

                if tokens and tokens.get("access_token") and tokens.get("refresh_token"):
                    self._log("OAuth 回退补全成功！")
                    workspace_id = ""
                    session_cookie = ""
                    try:
                        session_data = oauth_client._decode_oauth_session_cookie()
                        if session_data:
                            workspaces = session_data.get("workspaces", [])
                            if workspaces:
                                workspace_id = str((workspaces[0] or {}).get("id") or "")
                                if workspace_id:
                                    self._log(f"成功萃取 Workspace ID: {workspace_id}")
                    except Exception:
                        pass

                    try:
                        for cookie in oauth_client.session.cookies.jar:
                            if cookie.name == "__Secure-next-auth.session-token":
                                session_cookie = cookie.value
                                break
                    except Exception:
                        pass

                    account_id = self._extract_account_id_from_token(tokens.get("access_token", "")) or workspace_id
                    return {
                        "success": True,
                        "state": "active",
                        "access_token": tokens.get("access_token", ""),
                        "refresh_token": tokens.get("refresh_token", ""),
                        "id_token": tokens.get("id_token", ""),
                        "account_id": account_id or ("v2_acct_" + chatgpt_client.device_id[:8]),
                        "workspace_id": workspace_id or account_id,
                        "session_token": session_cookie,
                        "metadata": {
                            "repair_metadata": self._build_repair_metadata(
                                chatgpt_client=chatgpt_client,
                                oauth_client=oauth_client,
                                state="active",
                            ),
                        },
                    }

                # 7. 手机号验证需求：按成功返回，但标记为待补全
                if oauth_client and self._is_phone_required_error(oauth_client.last_error):
                    self._log("检测到手机号验证需求，按待补全状态返回")
                    return self._build_salvage_result(
                        state="pending_phone",
                        chatgpt_client=chatgpt_client,
                        oauth_client=oauth_client,
                        metadata={
                            "phone_verification_required": True,
                            "token_pending": True,
                        },
                        error_message=oauth_client.last_error,
                    )

                if oauth_client and self._is_account_deactivated_error(oauth_client.last_error):
                    return {"success": False, "error_message": "account_deactivated"}

                if add_phone_required or interrupted_for_reauth:
                    pending_metadata = {"token_pending": True}
                    if add_phone_required:
                        pending_metadata["phone_verification_required"] = True
                    self._log(f"OAuth 补全过程未完成，按 {salvage_state} 返回待修复上下文")
                    return self._build_salvage_result(
                        state=salvage_state,
                        chatgpt_client=chatgpt_client,
                        oauth_client=oauth_client,
                        metadata=pending_metadata,
                        error_message=getattr(oauth_client, "last_error", "") or str(session_result or ""),
                    )

                last_error = str(getattr(oauth_client, "last_error", "") or "").strip() or "获取最终 OAuth Tokens 失败"
                return {"success": False, "error_message": f"账号已创建成功，但 {last_error}"}

            except Exception as attempt_error:
                last_error = str(attempt_error)
                if attempt < self.max_retries - 1 and self._should_retry(last_error):
                    self._log(f"本轮出现异常，准备整流程重试: {last_error}")
                    continue
                return {"success": False, "error_message": last_error}

        return {"success": False, "error_message": last_error or "注册失败"}
