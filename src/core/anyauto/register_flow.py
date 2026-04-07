"""
Any-auto-register 风格注册流程（V2）。
以状态机 + Session 复用为主，必要时回退 OAuth。
"""

from __future__ import annotations

import secrets
import string
import time
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
            "registration_disallowed",
            "cannot create your account with the given information",
            "不允许继续创建账号",
            "当前出口 ip / 设备指纹 / 会话环境很可能触发风控",
            "create-account/password 阶段",
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

    def _passwordless_oauth_reauth(
        self,
        chatgpt_client: ChatGPTClient,
        email: str,
        skymail_adapter: EmailServiceAdapter,
        oauth_config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        self._log("检测到 add_phone，尝试 passwordless OTP 登录补全 workspace...")
        oauth_client = OAuthClient(
            config=oauth_config,
            proxy=self.proxy_url,
            verbose=False,
            browser_mode=self.browser_mode,
        )
        oauth_client._log = self._log

        tokens = oauth_client.login_passwordless_and_get_tokens(
            email,
            chatgpt_client.device_id,
            chatgpt_client.ua,
            chatgpt_client.sec_ch_ua,
            chatgpt_client.impersonate,
            skymail_adapter,
        )
        if tokens and tokens.get("access_token"):
            return {
                "access_token": tokens.get("access_token", ""),
                "refresh_token": tokens.get("refresh_token", ""),
                "id_token": tokens.get("id_token", ""),
                "session": oauth_client.session,
            }

        if oauth_client.last_error:
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
                    self._log("开始注册流程 V2 (Session 复用直取 AccessToken)")
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

                if add_phone_required or interrupted_for_reauth:
                    if interrupted_for_reauth:
                        self._log("🔥 [CODEX] 捕获到注册中断信号，立即启动 Passwordless OAuth 接力流程...")
                    else:
                        self._log("检测到账号需要手机号验证，尝试通过 Passwordless OAuth 补全流程...")
                        
                    pwdless = self._passwordless_oauth_reauth(
                        chatgpt_client,
                        normalized_email,
                        skymail_adapter,
                        oauth_config,
                    )
                    if pwdless and pwdless.get("access_token"):
                        self.session = pwdless.get("session") or self.session
                        return {
                            "success": True,
                            "access_token": pwdless.get("access_token", ""),
                            "refresh_token": pwdless.get("refresh_token", ""),
                            "id_token": pwdless.get("id_token", ""),
                        }

                # 5. 复用 session 取 token
                self._log("步骤 2/2: 优先复用注册会话提取 ChatGPT Session / AccessToken...")
                session_ok, session_result = chatgpt_client.reuse_session_and_get_tokens()
                if session_ok:
                    self._log(">>> [CODEX-VER-2026-04-05-A] 进入令牌提取阶段 <<<")
                    # 强制使用原生库的 OAuth 登录流进行补全 (最稳妥方案)
                    has_rt = bool(chatgpt_client.refresh_token and str(chatgpt_client.refresh_token).strip())
                    if not has_rt:
                        self._log("🚀 关键补丁：检测到缺失 RT，启动原生 OAuthClient 深度提取流程...")
                        try:
                            from .oauth_client import OAuthClient
                            # 1. 准备配置 (对齐 auth.openai.com 规范)
                            oauth_settings = {
                                "oauth_issuer": "https://auth.openai.com",
                                "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                                "oauth_redirect_uri": "http://localhost:1455/auth/callback",
                            }
                            # 2. 实例化原生 OAuth 客户端
                            oc = OAuthClient(
                                config=oauth_settings,
                                proxy=self.proxy_url,
                                verbose=True,
                                browser_mode=self.browser_mode
                            )
                            oc._log = self._log
                            
                            # 2.5 核心补丁：重置邮件获取的起始时间，确保只拿“新”验证码
                            import time
                            if skymail_adapter:
                                if hasattr(skymail_adapter, "reset_start_time"):
                                    skymail_adapter.reset_start_time()
                                else:
                                    self._log("💡 正在尝试重置 Skymail 获取时间戳...")
                                    # 兼容性写法，确保不同版本的 adapter 都能识别
                                    setattr(skymail_adapter, "_start_time", time.time())
                                    setattr(skymail_adapter, "last_code", None)
                            
                            # 3. 执行完整的登录 & 令牌获取流程 (包含自动 OTP 获取)
                            # 增加超时到 120 秒，应对 OpenAI 延迟
                            tokens = oc.login_and_get_tokens(
                                email=self.email,
                                password=self.password,
                                device_id=chatgpt_client.device_id,
                                user_agent=chatgpt_client.ua,
                                sec_ch_ua=chatgpt_client.sec_ch_ua,
                                impersonate=chatgpt_client.impersonate,
                                skymail_client=skymail_adapter
                            )
                            
                            if tokens and tokens.get("refresh_token"):
                                chatgpt_client.refresh_token = tokens["refresh_token"]
                                self._log(f"🔥 [SUCCESS] 原生 OAuth 流程成功捕获 RT: {chatgpt_client.refresh_token[:20]}...")
                            else:
                                self._log("❌ 原生 OAuth 流程未能捕获 RT，尝试返回现有 AccessToken")
                        except Exception as e:
                            self._log(f"❌ 原生 OAuth 补全链路崩溃: {e}")
                    else:
                        self._log(f"✅ 已持有持久化 RT: {chatgpt_client.refresh_token[:20]}...")

                    self._log("Token 提取完成！")
                    account_id = str(session_result.get("account_id", "") or "").strip()
                    if not account_id:
                        account_id = str(session_result.get("workspace_id", "") or "").strip()
                    if not account_id:
                        account_id = self._extract_account_id_from_token(session_result.get("access_token", ""))
                    workspace_id = str(session_result.get("workspace_id", "") or "").strip() or account_id
                    return {
                        "success": True,
                        "access_token": session_result.get("access_token", ""),
                        "refresh_token": chatgpt_client.refresh_token or session_result.get("refresh_token", ""),
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

                # 6. OAuth 回退
                self._log(f"复用会话失败，回退到 OAuth 登录补全流程: {session_result}")
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
                    oauth_client.session = chatgpt_client.session

                    tokens = oauth_client.login_and_get_tokens(
                        normalized_email,
                        self.password,
                        chatgpt_client.device_id,
                        chatgpt_client.ua,
                        chatgpt_client.sec_ch_ua,
                        chatgpt_client.impersonate,
                        skymail_adapter,
                    )
                    if tokens and tokens.get("access_token"):
                        break

                    if oauth_client.last_error and "add_phone" in oauth_client.last_error:
                        break

                if tokens and tokens.get("access_token"):
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
                        "access_token": tokens.get("access_token", ""),
                        "refresh_token": tokens.get("refresh_token", ""),
                        "id_token": tokens.get("id_token", ""),
                        "account_id": account_id or ("v2_acct_" + chatgpt_client.device_id[:8]),
                        "workspace_id": workspace_id or account_id,
                        "session_token": session_cookie,
                    }

                # 7. 手机号验证需求：按成功返回，但标记为待补全
                if oauth_client and self._is_phone_required_error(oauth_client.last_error):
                    self._log("检测到手机号验证需求，按成功返回并标记待补全")
                    return {
                        "success": True,
                        "metadata": {
                            "phone_verification_required": True,
                            "token_pending": True,
                            "oauth_error": oauth_client.last_error,
                        },
                    }

                last_error = str(getattr(oauth_client, "last_error", "") or "").strip() or "获取最终 OAuth Tokens 失败"
                return {"success": False, "error_message": f"账号已创建成功，但 {last_error}"}

            except Exception as attempt_error:
                last_error = str(attempt_error)
                if attempt < self.max_retries - 1 and self._should_retry(last_error):
                    self._log(f"本轮出现异常，准备整流程重试: {last_error}")
                    continue
                return {"success": False, "error_message": last_error}

        return {"success": False, "error_message": last_error or "注册失败"}
