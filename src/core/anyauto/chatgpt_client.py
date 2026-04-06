"""
ChatGPT 注册客户端模块
使用 curl_cffi 模拟浏览器行为
"""

import random
import uuid
import time
from urllib.parse import urlparse

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    print("❌ 需要安装 curl_cffi: pip install curl_cffi")
    import sys
    sys.exit(1)

from ..openai.sentinel_browser import fetch_browser_sentinel_artifacts
from .utils import (
    FlowState,
    build_browser_headers,
    decode_jwt_payload,
    describe_flow_state,
    extract_flow_state,
    generate_datadog_trace,
    normalize_flow_url,
    random_delay,
    seed_oai_device_cookie,
)


# Chrome 指纹配置
_CHROME_PROFILES = [
    {
        "major": 131, "impersonate": "chrome131",
        "build": 6778, "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133, "impersonate": "chrome133a",
        "build": 6943, "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136, "impersonate": "chrome136",
        "build": 7103, "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
]


def _random_chrome_version():
    """随机选择一个 Chrome 版本"""
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


def _format_environment_rejection_message(request_id, retry_scope="直接"):
    message = (
        f"OpenAI 在 create-account/password 阶段{retry_scope}拒绝当前注册请求，"
        "当前出口 IP / 设备指纹 / 会话环境很可能触发风控"
    )
    resolved_request_id = str(request_id or "").strip()
    if resolved_request_id:
        return f"{message}（x-request-id: {resolved_request_id}）"
    return message


class ChatGPTClient:
    """ChatGPT 注册客户端"""
    
    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"
    
    def __init__(self, proxy=None, verbose=True, browser_mode="protocol"):
        """
        初始化 ChatGPT 客户端

        Args:
            proxy: 代理地址
            verbose: 是否输出详细日志
            browser_mode: protocol | headless | headed
        """
        self.proxy = proxy
        self.verbose = verbose
        self.browser_mode = browser_mode or "protocol"
        self.device_id = str(uuid.uuid4())
        self.refresh_token = ""  # 初始化为空字符串
        self.last_code_verifier = None # 保存 PKCE 验证码
        self.accept_language = random.choice([

            "en-US,en;q=0.9",
            "en-US,en;q=0.9,zh-CN;q=0.8",
            "en,en-US;q=0.9",
            "en-US,en;q=0.8",
        ])
        
        # 随机 Chrome 版本
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()
        
        # 创建 session
        self.session = curl_requests.Session(impersonate=self.impersonate)
        
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        
        # 设置基础 headers
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": self.accept_language,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{self.chrome_full}"',
            "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
        })
        
        # 设置 oai-did cookie
        seed_oai_device_cookie(self.session, self.device_id)
        self.last_registration_state = FlowState()
    
    def _log(self, msg):
        """输出日志"""
        if self.verbose:
            print(f"  {msg}")

    def _sniff_refresh_token(self, response):
        """
        从响应中嗅探 refresh_token。
        检查 Set-Cookie 头以及响应体。
        """
        url = str(response.url)
        # 1. 检查 Set-Cookie (最优先，通常 RT 在这里)
        # curl_cffi 会自动合并 set-cookie，我们也可以手动检查 headers
        sc_headers = response.headers.get_list("Set-Cookie") if hasattr(response.headers, "get_list") else [response.headers.get("Set-Cookie", "")]
        
        # 调试：记录所有 Set-Cookie 头部和特定 URL 的响应体
        debug_log = "/app/logs/cookie_sniffer.txt"
        try:
            import time
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {url}\n")
                for sc in sc_headers:
                    if sc:
                        f.write(f"  Cookie: {sc[:250]}\n")
                
                # 记录内存中的所有 Cookies (包括刚刚注入的)
                for cookie in self.session.cookies.jar:
                    val = str(cookie.value or "")
                    if "oaistb" in val or "oaistb" in str(cookie.name):
                        f.write(f"  🔥 MEMORY COOKIE: {cookie.name}={val[:100]}...\n")
                        if val.startswith("oaistb_rt_"):
                            self.refresh_token = val
                            f.write(f"  🔥 AUTO CAPTURED RT: {val}\n")
                
                # 记录更多接口的响应体 (可能包含隐藏的 RT)
                if any(x in url for x in ["/api/auth/", "auth.openai.com", "/api/v1/"]):
                    try:
                        content = response.text[:2000]
                        f.write(f"  Body (Preview): {content}\n")
                        # 特别记录 Header
                        if "callback" in url:
                            f.write(f"  FULL HEADERS: {dict(response.headers)}\n")
                    except:
                        pass
        except Exception as e:
            self._log(f"Debug log fail: {e}")

        import re
        for sc in sc_headers:
            if not sc: continue
            # 匹配 oaistb_rt_ 或者标准的 refresh-token
            match = re.search(r'(oaistb_rt_[^;=\s\?]+)', sc)
            if match:
                self.refresh_token = match.group(1)
                self._log(f"🔥 嗅探器从 Set-Cookie 捕获到 RT: {self.refresh_token[:15]}...")
                try:
                    with open(debug_log, "a", encoding="utf-8") as f:
                        f.write(f"  🔥 SUCCESS CAPTURED: {self.refresh_token}\n")
                except:
                    pass
                return

        # 2. 检查响应体 JSON
        try:
            if "application/json" in response.headers.get("Content-Type", "").lower():
                data = response.json()
                rt = (
                    data.get("refresh_token") 
                    or data.get("refreshToken") 
                    or (data.get("session") or {}).get("refresh_token")
                )
                if rt and str(rt).startswith("oaistb_rt_"):
                    self.refresh_token = str(rt)
                    self._log(f"🔥 嗅探器从 JSON 捕获到 RT: {self.refresh_token[:15]}...")
                    return
        except:
            pass

    def _browser_pause(self, low=0.15, high=0.45):
        """在 headed 模式下加入轻微停顿，模拟有头浏览器节奏。"""
        if self.browser_mode == "headed":
            random_delay(low, high)

    def _headers(
        self,
        url,
        *,
        accept,
        referer=None,
        origin=None,
        content_type=None,
        navigation=False,
        fetch_mode=None,
        fetch_dest=None,
        fetch_site=None,
        extra_headers=None,
    ):
        full_extra = {
            "oai-did": self.device_id,
            "X-OpenAI-Device-Id": self.device_id,
        }
        if extra_headers:
            full_extra.update(extra_headers)
            
        return build_browser_headers(
            url=url,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            chrome_full_version=self.chrome_full,
            accept=accept,
            accept_language=self.accept_language,
            referer=referer,
            origin=origin,
            content_type=content_type,
            navigation=navigation,
            fetch_mode=fetch_mode,
            fetch_dest=fetch_dest,
            fetch_site=fetch_site,
            headed=self.browser_mode == "headed",
            extra_headers=full_extra,
        )

    def _reset_session(self):
        """重置浏览器指纹与会话，用于绕过偶发的 Cloudflare/SPA 中间页。"""
        self.device_id = str(uuid.uuid4())
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()
        self.accept_language = random.choice([
            "en-US,en;q=0.9",
            "en-US,en;q=0.9,zh-CN;q=0.8",
            "en,en-US;q=0.9",
            "en-US,en;q=0.8",
        ])

        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": self.accept_language,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{self.chrome_full}"',
            "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
        })
        seed_oai_device_cookie(self.session, self.device_id)

    def _browser_sentinel_headless(self) -> bool:
        if self.browser_mode == "headed":
            return False
        import os
        return not bool(os.environ.get("DISPLAY"))

    def _fetch_browser_sentinel_artifacts(
        self,
        *,
        flow: str,
        page_url: str,
        include_session_observer: bool = False,
        include_passkey_capabilities: bool = False,
    ):
        return fetch_browser_sentinel_artifacts(
            flow=flow,
            device_id=self.device_id,
            page_url=page_url,
            proxy=self.proxy,
            user_agent=self.ua,
            accept_language=self.accept_language,
            include_session_observer=include_session_observer,
            include_passkey_capabilities=include_passkey_capabilities,
            headless=self._browser_sentinel_headless(),
        )

    def _state_from_url(self, url, method="GET"):
        state = extract_flow_state(
            current_url=normalize_flow_url(url, auth_base=self.AUTH),
            auth_base=self.AUTH,
            default_method=method,
        )
        if method:
            state.method = str(method).upper()
        return state

    def _state_from_payload(self, data, current_url=""):
        return extract_flow_state(
            data=data,
            current_url=current_url,
            auth_base=self.AUTH,
        )

    def _state_signature(self, state: FlowState):
        return (
            state.page_type or "",
            state.method or "",
            state.continue_url or "",
            state.current_url or "",
        )

    def _is_registration_complete_state(self, state: FlowState):
        current_url = (state.current_url or "").lower()
        continue_url = (state.continue_url or "").lower()
        page_type = state.page_type or ""
        return (
            page_type in {"callback", "chatgpt_home", "oauth_callback"}
            or ("chatgpt.com" in current_url and "redirect_uri" not in current_url)
            or ("chatgpt.com" in continue_url and "redirect_uri" not in continue_url and page_type != "external_url")
        )

    def _state_is_password_registration(self, state: FlowState):
        return state.page_type in {"create_account_password", "password"}

    def _state_is_email_otp(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "email_otp_verification" or "email-verification" in target or "email-otp" in target

    def _state_is_about_you(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "about_you" or "about-you" in target

    def _state_is_add_phone(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "add_phone" or "add-phone" in target

    def _state_requires_navigation(self, state: FlowState):
        if (state.method or "GET").upper() != "GET":
            return False
        if state.page_type == "external_url" and state.continue_url:
            return True
        if state.continue_url and state.continue_url != state.current_url:
            return True
        return False

    def _follow_flow_state(self, state: FlowState, referer=None):
        """跟随服务端返回的 continue_url，推进注册状态机。"""
        target_url = state.continue_url or state.current_url
        if not target_url:
            return False, "缺少可跟随的 continue_url"

        try:
            self._browser_pause()
            r = self.session.get(
                target_url,
                headers=self._headers(
                    target_url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=referer,
                    navigation=True,
                ),
                allow_redirects=False, # 禁用自动跟随，手动追踪每一跳
                timeout=30,
            )
            
            # 手动追踪重定向链，确保每一跳都能嗅探到 RT
            max_hops = 10
            for _ in range(max_hops):
                self._sniff_refresh_token(r)
                if r.status_code not in (301, 302, 303, 307, 308):
                    break
                
                location = r.headers.get("Location")
                if not location:
                    break
                
                # 处理相对路径
                from urllib.parse import urljoin
                target_url = urljoin(str(r.url), location)
                self._log(f"重定向跳转 -> {target_url}")
                
                r = self.session.get(
                    target_url,
                    headers=self._headers(
                        target_url,
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=str(r.url),
                        navigation=True,
                    ),
                    allow_redirects=False,
                    timeout=30,
                )

            final_url = str(r.url)
            self._log(f"follow -> {r.status_code} {final_url}")

            content_type = (r.headers.get("content-type", "") or "").lower()
            if "application/json" in content_type:
                try:
                    next_state = self._state_from_payload(r.json(), current_url=final_url)
                except Exception:
                    next_state = self._state_from_url(final_url)
            else:
                next_state = self._state_from_url(final_url)

            self._log(f"follow state -> {describe_flow_state(next_state)}")
            return True, next_state
        except Exception as e:
            self._log(f"跟随 continue_url 失败: {e}")
            return False, str(e)

    def _get_cookie_value(self, name, domain_hint=None):
        """读取当前会话中的 Cookie。"""
        for cookie in self.session.cookies.jar:
            if cookie.name != name:
                continue
            if domain_hint and domain_hint not in (cookie.domain or ""):
                continue
            return cookie.value
        return ""

    def get_next_auth_session_token(self):
        """获取 ChatGPT next-auth 会话 Cookie。"""
        return self._get_cookie_value("__Secure-next-auth.session-token", "chatgpt.com")

    def extract_refresh_token_from_cookies(self):
        """从 Cookies 中尝试提取 refresh_token（支持 oaistb_rt_ 前缀）。"""
        self._log(f"开始从 {len(self.session.cookies.jar)} 个 Cookies 中提取 refresh_token...")
        for cookie in self.session.cookies.jar:
            name = str(cookie.name or "").lower()
            val = str(cookie.value or "").strip()
            
            # 日志：记录每个 cookie 的名称和值的前缀（脱敏）
            display_val = f"{val[:12]}..." if len(val) > 12 else val
            self._log(f"  Cookie: {cookie.name} = {display_val} (domain={cookie.domain})")
            
            if val.startswith("oaistb_rt_"):
                self._log(f"找到以 oaistb_rt_ 开头的 Cookie 值 (Name: {cookie.name})")
                return val
            if "refresh-token" in name or "refreshtoken" in name:
                self._log(f"找到名称包含 refresh-token 的 Cookie: {cookie.name}")
                return val
        return ""

    def fetch_chatgpt_session(self):
        """请求 ChatGPT Session 接口并返回原始会话数据。"""
        url = f"{self.BASE}/api/auth/session"
        self._browser_pause()
        
        # 增强：在请求 Session 接口时，注入 oai-did 头部 (这是获取 RT 的关键)
        extra = {
            "oai-did": self.device_id,
            "X-OpenAI-Device-Id": self.device_id,
        }
        
        response = self.session.get(
            url,
            headers=self._headers(
                url,
                accept="application/json",
                referer=f"{self.BASE}/",
                fetch_site="same-origin",
                extra_headers=extra
            ),
            timeout=30,
        )
        self._sniff_refresh_token(response)
        
        if response.status_code != 200:
            return False, f"/api/auth/session -> HTTP {response.status_code}"

        try:
            data = response.json()
            # 记录接口返回的关键字段，用于调试
            keys = list(data.keys())
            self._log(f"/api/auth/session 响应字段: {keys}")
        except Exception as exc:
            return False, f"/api/auth/session 返回非 JSON: {exc}"

        access_token = str(data.get("accessToken") or "").strip()
        if not access_token:
            return False, "/api/auth/session 未返回 accessToken"
            
        # 核心：从 session 响应中提取 refreshToken (优先检查多个可能字段)
        api_refresh_token = (
            str(data.get("refreshToken") or "").strip()
            or str(data.get("refresh_token") or "").strip()
            or str((data.get("session") or {}).get("refresh_token") or "").strip()
        )
        
        if api_refresh_token and "oaistb_rt_" in api_refresh_token:
            self.refresh_token = api_refresh_token
            self._log(f"🔥 成功从 /api/auth/session 提取到 RT: {self.refresh_token[:15]}...")
            
        return True, data

    def perform_secondary_login(self, email, password, skymail_adapter=None):
        """
        注册成功后执行二次登录，以获取持久化的 refresh_token。
        """
        self._log(f"--- 启动二次登录获取 RT: {email} ---")
        
        # 1. 彻底隔离会话 (但保留 device_id)
        current_did = self.device_id
        current_ua = self.ua
        self._reset_session()
        self.device_id = current_did
        self.ua = current_ua
        seed_oai_device_cookie(self.session, self.device_id)
        
        # 2. 访问登录入口
        login_url = f"{self.BASE}/auth/login"
        self._log(f"二次登录: 访问登录入口 {login_url} ...")
        try:
            r = self.session.get(
                login_url,
                headers=self._headers(
                    login_url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    navigation=True,
                ),
                timeout=30,
            )
            self._sniff_refresh_token(r)
        except Exception as e:
            self._log(f"二次登录: 访问登录入口异常: {e}")
            
        # 3. 获取 CSRF Token
        csrf = self.get_csrf_token()
        if not csrf:
            self._log("二次登录: 获取 CSRF 失败")
            return False
        
        # 4. 发起 Signin 获取 authorize URL
        auth_url = self.signin(email, csrf)
        if not auth_url:
            self._log("二次登录: Signin 失败")
            return False
        
        # 5. 自适应状态机驱动
        success, state = self._follow_flow_state(FlowState(continue_url=auth_url))
        if not success:
            self._log("二次登录: 初始跟随失败")
            return False
            
        # 循环推进状态机，直到进入落地页或报错
        max_steps = 5
        for step in range(max_steps):
            page_type = state.page_type
            self._log(f"二次登录状态机 Step {step+1}: {page_type}")
            
            if self._is_registration_complete_state(state):
                self._log("二次登录: 已到达落地状态")
                break
                
            if page_type == "login_password":
                # 提交密码
                self._log("二次登录: 正在提交密码验证...")
                payload = {"username": email, "password": password}
                r = self.session.post(
                    f"{self.AUTH}/api/accounts/login/password",
                    headers=self._headers(
                        f"{self.AUTH}/api/accounts/login/password",
                        accept="application/json",
                        referer=str(state.current_url),
                        content_type="application/json",
                    ),
                    json=payload,
                    allow_redirects=False,
                    timeout=30
                )
                self._sniff_refresh_token(r)
                if r.status_code == 200:
                    state = self._state_from_payload(r.json(), current_url=str(r.url))
                elif r.status_code in (301, 302, 303, 307, 308):
                    state = self._state_from_url(r.headers.get("Location"))
                else:
                    self._log(f"二次登录: 密码提交异常 (HTTP {r.status_code})")
                    break
            elif page_type == "email_otp_verification":
                self._log("二次登录: 检测到需要邮箱验证，尝试跟随...")
                success, state = self._follow_flow_state(state)
                if not success: break
            else:
                # 通用跟随
                success, state = self._follow_flow_state(state)
                if not success: break
        
        # 6. 最终 Session 握手
        self._log("二次登录: 正在通过 /api/auth/session 提取最终令牌...")
        success, _ = self.fetch_chatgpt_session()
        
        if success and self.refresh_token:
            self._log(f"🔥 黄金路径达成！二次登录成功捕获 RT: {self.refresh_token[:15]}...")
            return True
            
        return False

    def reuse_session_and_get_tokens(self):
        """
        复用注册阶段已建立的 ChatGPT 会话，直接读取 Session / AccessToken。
        """
        state = self.last_registration_state or FlowState()
        self._log("步骤 1/4: 跟随注册回调 external_url ...")
        
        # 记录回调 URL 以便后续手动交换（如果自动跟随没抓到 RT）
        callback_url = state.continue_url or state.current_url
        
        if state.page_type == "external_url" or self._state_requires_navigation(state):
            ok, followed = self._follow_flow_state(
                state,
                referer=state.current_url or f"{self.AUTH}/about-you",
            )
            if not ok:
                return False, f"注册回调落地失败: {followed}"
            self.last_registration_state = followed
        else:
            self._log("注册回调已落地，跳过额外跟随")

        # 检查是否已经抓到 RT
        if not self.refresh_token and callback_url and "code=" in callback_url:
            self._log("⚠️ 自动跟随未捕获到 RT，尝试手动提取 code 进行 OAuth 交换...")
            try:
                from .oauth_client import OAuthClient
                # 使用相同的 session 和指纹
                oauth = OAuthClient(config={}, proxy=self.proxy, verbose=self.verbose)
                oauth.session = self.session 
                
                # 尝试从 URL 提取 code
                import urllib.parse
                parsed = urllib.parse.urlparse(callback_url)
                code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
                
                if code and self.last_code_verifier:
                    self._log(f"🔥 手动提取到 Code: {code[:15]}... 正在利用 PKCE Verifier 强制换码")
                    # 准备正确的 OAuth 配置 (OpenAI Web App 默认值)
                    client_id = getattr(self, "client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
                    redirect_uri = getattr(self, "redirect_uri", f"{self.BASE}/api/auth/callback/openai")
                    
                    config = {
                        "oauth_issuer": "https://auth.openai.com",
                        "oauth_client_id": client_id,
                        "oauth_redirect_uri": redirect_uri,
                    }
                    from .oauth_client import OAuthClient
                    oauth = OAuthClient(config=config, proxy=self.proxy, verbose=True)
                    oauth.session = self.session # 复用同一个 Session 以维持指纹一致性
                    
                    # 使用注册阶段生成的 code_verifier 进行交换
                    res = oauth._exchange_code_for_tokens(code, code_verifier=self.last_code_verifier, user_agent=self.ua, impersonate=self.impersonate)
                    if res and res.get("refresh_token"):
                        self.refresh_token = res["refresh_token"]
                        self._log(f"🔥 [SUCCESS] 手动 OAuth 交换成功获取到 RT: {self.refresh_token[:20]}...")
                    else:
                        self._log("⚠️ 手动交换未获取到 RT，可能账号已被标记 add_phone 或环境受阻")
                elif code:
                    self._log("⚠️ 提取到 Code 但缺少本地 Verifier，这通常是因为 signin 阶段注入失败")
            except Exception as e:
                self._log(f"手动 OAuth 交换尝试失败: {e}")

        self._log("步骤 2/4: 检查 __Secure-next-auth.session-token ...")

        session_cookie = self.get_next_auth_session_token()
        if not session_cookie:
            return False, "缺少 __Secure-next-auth.session-token，注册回调可能未落地"

        # 尝试从 Cookie 中补齐 refresh_token（针对新版 oaistb_rt_）
        if not self.refresh_token:
            self.refresh_token = self.extract_refresh_token_from_cookies()
            if self.refresh_token:
                self._log("从 Cookies 中提取到 refresh_token")

        self._log("步骤 3/4: 请求 ChatGPT /api/auth/session ...")
        ok, session_or_error = self.fetch_chatgpt_session()
        if not ok:
            return False, session_or_error

        session_data = session_or_error
        access_token = str(session_data.get("accessToken") or "").strip()
        session_token = str(session_data.get("sessionToken") or session_cookie or "").strip()
        user = session_data.get("user") or {}
        account = session_data.get("account") or {}
        jwt_payload = decode_jwt_payload(access_token)
        auth_payload = jwt_payload.get("https://api.openai.com/auth") or {}

        account_id = (
            str(account.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_account_id") or "").strip()
        )
        user_id = (
            str(user.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_user_id") or "").strip()
            or str(auth_payload.get("user_id") or "").strip()
        )

        normalized = {
            "access_token": access_token,
            "refresh_token": self.refresh_token,
            "session_token": session_token,
            "account_id": account_id,
            "user_id": user_id,
            "workspace_id": account_id,
            "expires": session_data.get("expires"),
            "user": user,
            "account": account,
            "auth_provider": session_data.get("authProvider"),
            "raw_session": session_data,
        }

        self._log("步骤 4/4: 已从复用会话中提取 accessToken")
        if account_id:
            self._log(f"Session Account ID: {account_id}")
        if user_id:
            self._log(f"Session User ID: {user_id}")
        return True, normalized
    
    def visit_homepage(self):
        """访问首页，建立 session"""
        self._log("访问 ChatGPT 首页...")
        url = f"{self.BASE}/"
        try:
            self._browser_pause()
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            return r.status_code == 200
        except Exception as e:
            self._log(f"访问首页失败: {e}")
            return False
    
    def get_csrf_token(self):
        """获取 CSRF token"""
        self._log("获取 CSRF token...")
        url = f"{self.BASE}/api/auth/csrf"
        try:
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=f"{self.BASE}/",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )
            
            if r.status_code == 200:
                data = r.json()
                token = data.get("csrfToken", "")
                if token:
                    self._log(f"CSRF token: {token[:20]}...")
                    return token
        except Exception as e:
            self._log(f"获取 CSRF token 失败: {e}")
        
        return None
    
    def signin(self, email, csrf_token):
        """
        提交邮箱，获取 authorize URL
        """
        self._log(f"提交邮箱: {email}")
        url = f"{self.BASE}/api/auth/signin/openai"
        
        params = {
            "prompt": "login",
            "ext-oai-did": self.device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "screen_hint": "login_or_signup",
            "login_hint": email,
        }
        
        form_data = {
            "callbackUrl": f"{self.BASE}/",
            "csrfToken": csrf_token,
            "json": "true",
        }

        try:
            self._browser_pause()
            r = self.session.post(
                url,
                params=params,
                data=form_data,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=f"{self.BASE}/",
                    origin=self.BASE,
                    content_type="application/x-www-form-urlencoded",
                    fetch_site="same-origin",
                ),
                timeout=30
            )
            
            if r.status_code == 200:
                data = r.json()
                authorize_url = data.get("url", "")
                if authorize_url:
                    self._log(f"获取到 authorize URL")
                    return authorize_url
        except Exception as e:
            self._log(f"提交邮箱失败: {e}")
        
        return None
    
    def authorize(self, url, max_retries=3):
        """
        访问 authorize URL，并强制注入我们控制的 PKCE 挑战码
        """
        import urllib.parse
        from .oauth_client import generate_pkce
        
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        
        # 核心：如果我们能生成自己的挑战码，就替换掉它
        code_verifier, code_challenge = generate_pkce()
        self.last_code_verifier = code_verifier
        self._log(f"🔥 [CODEX] 正在拦截 Authorize URL 并注入自定义 PKCE 挑战码...")
        
        query["code_challenge"] = [code_challenge]
        query["code_challenge_method"] = ["S256"]
        
        # 重组 URL
        new_query = urllib.parse.urlencode(query, doseq=True)
        new_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
        
        url = new_url # 使用拦截后的新 URL

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    self._log(f"访问 authorize URL... (尝试 {attempt + 1}/{max_retries})")
                    time.sleep(1)  # 重试前等待
                else:
                    self._log("访问 authorize URL...")

                self._browser_pause()
                r = self.session.get(
                    url,
                    headers=self._headers(
                        url,
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=f"{self.BASE}/",
                        navigation=True,
                    ),
                    allow_redirects=True,
                    timeout=30,
                )
                
                final_url = str(r.url)
                self._log(f"重定向到: {final_url}")
                return final_url
                
            except Exception as e:
                error_msg = str(e)
                is_tls_error = "TLS" in error_msg or "SSL" in error_msg or "curl: (35)" in error_msg
                
                if is_tls_error and attempt < max_retries - 1:
                    self._log(f"Authorize TLS 错误 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}")
                    continue
                else:
                    self._log(f"Authorize 失败: {e}")
                    return ""
        
        return ""
    
    def callback(self, callback_url=None, referer=None):
        """完成注册回调"""
        self._log("执行回调...")
        url = callback_url or f"{self.AUTH}/api/accounts/authorize/callback"
        ok, _ = self._follow_flow_state(
            self._state_from_url(url),
            referer=referer or f"{self.AUTH}/about-you",
        )
        return ok
    
    def register_user(self, email, password):
        """
        注册用户（邮箱 + 密码）
        
        Returns:
            tuple: (success, message)
        """
        self._log(f"注册用户: {email}")
        url = f"{self.AUTH}/api/accounts/user/register"
        
        sentinel_header = None
        sentinel = None
        try:
            import os
            import sys
            sys.path.append(os.path.abspath("src"))
            from core.openai.sentinel_token_v2 import build_sentinel_token
            # 提供必要的 accept 参数以符合方法定义
            user_agent = self._headers(url, accept="application/json").get("user-agent", "")
            self._log(f"尝试使用纯 Python PoW (Node VM) 获取 Token, flow=username_password_create")
            
            # 增加捕获 stdout/stderr 的机制以便诊断
            sentinel_header = build_sentinel_token(
                self.session, 
                self.device_id, 
                flow="username_password_create", 
                user_agent=user_agent,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate
            )
            
            if sentinel_header:
                self._log("使用纯 Python PoW 算法获取 Sentinel Token 成功")
            else:
                # 记录失败的具体原因（如果库支持）
                self._log("纯 Python PoW 算法返回了空 Token，检查 Node 环境或 SDK 版本", "warning")
        except Exception as e:
            self._log(f"纯 Python PoW 算法执行崩溃: {e}", "error")
            
        if not sentinel_header:
            self._log("正在启动浏览器获取 Sentinel Token (降级方案)...")
            sentinel = self._fetch_browser_sentinel_artifacts(
                flow="username_password_create",
                page_url=f"{self.AUTH}/create-account/password",
            )
            sentinel_header = sentinel.token if sentinel else "{}"
            self._log(f"浏览器获取 Sentinel Token 结束, success={bool(sentinel)}")

        # 尝试获取当前的完整 URL 以补全 Referer
        current_referer = f"{self.AUTH}/create-account/password"
        state = self.last_registration_state
        if state and state.current_url and "create-account/password" in state.current_url:
            current_referer = state.current_url

        headers = self._headers(
            url,
            accept="application/json",
            referer=current_referer,
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={
                "oai-device-id": self.device_id,
                "OpenAI-Device-Id": self.device_id,
                "openai-sentinel-token": sentinel_header,
            },
        )

        headers.update(generate_datadog_trace())
        
        payload = {
            "username": email,
            "password": password,
        }
        
        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30)
            self._sniff_refresh_token(r)
            
            if r.status_code == 200:
                data = r.json()
                self._log("注册成功")
                return True, "注册成功"
            else:
                request_id = str(r.headers.get("x-request-id") or "").strip()
                try:
                    error_data = r.json()
                    error_msg = error_data.get("error", {}).get("message", r.text[:200])
                    error_code = error_data.get("error", {}).get("code", "")
                except:
                    error_msg = r.text[:200]
                    error_code = ""

                normalized_error_msg = str(error_msg or "").strip()
                normalized_error_code = str(error_code or "").strip().lower()
                lowered_error_msg = normalized_error_msg.lower()
                if request_id:
                    self._log(f"register_user 请求 ID: {request_id}")

                if normalized_error_code == "registration_disallowed" or (
                    "cannot create your account with the given information" in lowered_error_msg
                ):
                    message = _format_environment_rejection_message(request_id)
                elif "failed to create account" in lowered_error_msg and r.status_code == 400:
                    message = _format_environment_rejection_message(request_id)
                else:
                    message = f"HTTP {r.status_code}: {normalized_error_msg}"

                self._log(f"注册失败: {r.status_code} - {message}")
                return False, message
                
        except Exception as e:
            self._log(f"注册异常: {e}")
            return False, str(e)
    
    def send_email_otp(self):
        """触发发送邮箱验证码"""
        self._log("触发发送验证码...")
        url = f"{self.AUTH}/api/accounts/email-otp/send"

        try:
            self._browser_pause()
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="application/json, text/plain, */*",
                    referer=f"{self.AUTH}/create-account/password",
                    fetch_site="same-origin",
                ),
                allow_redirects=True,
                timeout=30,
            )
            return r.status_code == 200
        except Exception as e:
            self._log(f"发送验证码失败: {e}")
            return False
    
    def verify_email_otp(self, otp_code, return_state=False):
        """
        验证邮箱 OTP 码
        
        Args:
            otp_code: 6位验证码
            
        Returns:
            tuple: (success, message)
        """
        self._log(f"验证 OTP 码: {otp_code}")
        url = f"{self.AUTH}/api/accounts/email-otp/validate"
        
        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/email-verification",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        headers.update(generate_datadog_trace())
        
        payload = {"code": otp_code}
        
        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30)
            self._sniff_refresh_token(r)
            
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                next_state = self._state_from_payload(data, current_url=str(r.url) or f"{self.AUTH}/about-you")
                self._log(f"验证成功 {describe_flow_state(next_state)}")
                return (True, next_state) if return_state else (True, "验证成功")
            else:
                try:
                    error_msg = r.text[:200]
                except Exception:
                    error_msg = ""
                self._log(f"验证失败: {r.status_code} - {error_msg}")
                return False, f"HTTP {r.status_code}: {error_msg}".strip()
                
        except Exception as e:
            self._log(f"验证异常: {e}")
            return False, str(e)
    
    def create_account(self, first_name, last_name, birthdate, return_state=False):
        """
        完成账号创建（提交姓名和生日）
        
        Args:
            first_name: 名
            last_name: 姓
            birthdate: 生日 (YYYY-MM-DD)
            
        Returns:
            tuple: (success, message)
        """
        name = f"{first_name} {last_name}"
        self._log(f"完成账号创建: {name}")
        url = f"{self.AUTH}/api/accounts/create_account"
        
        sentinel_header = None
        sentinel = None
        try:
            import os
            import sys
            sys.path.append(os.path.abspath("src"))
            from core.openai.sentinel_token_v2 import build_sentinel_token
            # 提供必要的 accept 参数以符合方法定义
            user_agent = self._headers(url, accept="application/json").get("user-agent", "")
            self._log(f"尝试使用纯 Python PoW (Node VM) 获取 Token, flow=oauth_create_account")
            sentinel_header = build_sentinel_token(
                self.session, 
                self.device_id, 
                flow="oauth_create_account", 
                user_agent=user_agent,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate
            )
            if sentinel_header:
                self._log("使用纯 Python PoW 算法获取 Sentinel Token 成功")
            else:
                self._log("纯 Python PoW 算法返回了空 Token，准备降级到浏览器", "warning")
        except Exception as e:
            self._log(f"纯 Python PoW 算法异常: {e}", "warning")
            
        if not sentinel_header:
            self._log("正在启动浏览器获取 Sentinel Token (降级方案)...")
            sentinel = self._fetch_browser_sentinel_artifacts(
                flow="oauth_create_account",
                page_url=f"{self.AUTH}/about-you",
                include_session_observer=True,
            )
            sentinel_header = sentinel.token if sentinel else "{}"
            self._log(f"浏览器获取 Sentinel Token 结束, success={bool(sentinel)}")

        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/about-you",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={
                "oai-device-id": self.device_id,
                "OpenAI-Sentinel-Token": sentinel_header,
            },
        )
        if sentinel and getattr(sentinel, 'session_observer_token', None):
            headers["OpenAI-Sentinel-SO-Token"] = sentinel.session_observer_token
        headers.update(generate_datadog_trace())
        
        payload = {
            "name": name,
            "birthdate": birthdate,
        }
        
        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30)
            self._sniff_refresh_token(r)
            
            if r.status_code == 200:
                try:
                    data = r.json()
                    # 记录响应字段以便调试
                    self._log(f"create_account 响应字段: {list(data.keys())}")
                except Exception:
                    data = {}
                
                # 尝试多种路径提取 refresh_token
                refresh_token = (
                    str(data.get("refresh_token") or "").strip()
                    or str(data.get("refreshToken") or "").strip()
                    or str((data.get("session") or {}).get("refresh_token") or "").strip()
                )
                
                if not refresh_token:
                    # 检查 Set-Cookie
                    set_cookie = r.headers.get("Set-Cookie", "")
                    if "oaistb_rt_" in set_cookie:
                        import re
                        match = re.search(r'(oaistb_rt_[^;=\s]+)', set_cookie)
                        if match:
                            refresh_token = match.group(1)
                            self._log("从 create_account Set-Cookie 中提取到 refresh_token")

                if refresh_token:
                    self.refresh_token = refresh_token
                    self._log("create_account 已捕获 refresh_token")

                next_state = self._state_from_payload(data, current_url=str(r.url) or self.BASE)
                self._log(f"账号创建成功 {describe_flow_state(next_state)}")
                return (True, next_state) if return_state else (True, "账号创建成功")
            else:
                error_msg = r.text[:200]
                self._log(f"创建失败: {r.status_code} - {error_msg}")
                return False, f"HTTP {r.status_code}"
                
        except Exception as e:
            self._log(f"创建异常: {e}")
            return False, str(e)
    
    def register_complete_flow(self, email, password, first_name, last_name, birthdate, skymail_client):
        """
        完整的注册流程（基于原版 run_register 方法）
        
        Args:
            email: 邮箱
            password: 密码
            first_name: 名
            last_name: 姓
            birthdate: 生日
            skymail_client: Skymail 客户端（用于获取验证码）
            
        Returns:
            tuple: (success, message)
        """
        from urllib.parse import urlparse
        
        max_auth_attempts = 3
        final_url = ""
        final_path = ""

        for auth_attempt in range(max_auth_attempts):
            if auth_attempt > 0:
                self._log(f"预授权阶段重试 {auth_attempt + 1}/{max_auth_attempts}...")
                self._reset_session()

            # 1. 访问首页
            if not self.visit_homepage():
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "访问首页失败"

            # 2. 获取 CSRF token
            csrf_token = self.get_csrf_token()
            if not csrf_token:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "获取 CSRF token 失败"

            # 3. 提交邮箱，获取 authorize URL
            auth_url = self.signin(email, csrf_token)
            if not auth_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "提交邮箱失败"

            # 4. 访问 authorize URL（关键步骤！）
            final_url = self.authorize(auth_url)
            if not final_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "Authorize 失败"

            final_path = urlparse(final_url).path
            self._log(f"Authorize → {final_path}")

            # /api/accounts/authorize 实际上常对应 Cloudflare 403 中间页，不要继续走 authorize_continue。
            if "api/accounts/authorize" in final_path or final_path == "/error":
                self._log(f"检测到 Cloudflare/SPA 中间页，准备重试预授权: {final_url[:160]}...")
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, f"预授权被拦截: {final_path}"

            break
        
        state = self._state_from_url(final_url)
        self._log(f"注册状态起点: {describe_flow_state(state)}")

        register_submitted = False
        otp_verified = False
        account_created = False
        seen_states = {}

        for _ in range(12):
            signature = self._state_signature(state)
            seen_states[signature] = seen_states.get(signature, 0) + 1
            if seen_states[signature] > 2:
                return False, f"注册状态卡住: {describe_flow_state(state)}"

            if self._is_registration_complete_state(state):
                self.last_registration_state = state
                self._log("✅ 注册流程完成")
                return True, "注册成功"

            if self._state_is_password_registration(state):
                self._log("全新注册流程")
                if register_submitted:
                    return False, "注册密码阶段重复进入"
                success, msg = self.register_user(email, password)
                if not success:
                    return False, f"注册失败: {msg}"
                register_submitted = True
                if not self.send_email_otp():
                    self._log("发送验证码接口返回失败，继续等待邮箱中的验证码...")
                state = self._state_from_url(f"{self.AUTH}/email-verification")
                continue

            if self._state_is_email_otp(state):
                self._log("进入收码阶段，总等待时间 300s，每 90s 自动重试发送...")
                
                # 重置收码起始时间，确保过滤掉旧邮件
                if hasattr(skymail_client, "reset_start_time"):
                    skymail_client.reset_start_time()
                
                otp_code = None
                max_total_wait = 300  # 延长到 5 分钟
                resend_interval = 90  # 90 秒重发一次
                start_wait_time = time.time()
                tried_codes = set()
                
                while time.time() - start_wait_time < max_total_wait:
                    # 尝试收码
                    otp_code = skymail_client.wait_for_verification_code(email, timeout=resend_interval)
                    if otp_code and otp_code not in tried_codes:
                        self._log(f"成功获取验证码: {otp_code}")
                        success, next_state = self.verify_email_otp(otp_code, return_state=True)
                        if success:
                            otp_verified = True
                            state = next_state
                            self.last_registration_state = state
                            break
                        else:
                            self._log(f"验证码 {otp_code} 验证失败，可能由于延迟导致码失效，继续收新码...")
                            tried_codes.add(otp_code)
                    
                    # 超时未收到或码无效，触发重发
                    self._log(f"已等待 {int(time.time() - start_wait_time)}s 未收到可用验证码，尝试重发 (Resend OTP)...")
                    self.send_email_otp()
                
                if otp_verified:
                    continue
                return False, f"收码或校验超时 ({max_total_wait}s)"

            if self._state_is_about_you(state):
                if account_created:
                    return False, "填写信息阶段重复进入"
                success, next_state = self.create_account(
                    first_name,
                    last_name,
                    birthdate,
                    return_state=True,
                )
                if not success:
                    return False, f"创建账号失败: {next_state}"
                account_created = True
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_is_add_phone(state):
                self._log("检测到 add_phone 阶段，交由后续登录补全流程处理")
                self.last_registration_state = state
                return True, "add_phone_required"

            if self._state_requires_navigation(state):
                success, next_state = self._follow_flow_state(
                    state,
                    referer=state.current_url or f"{self.AUTH}/about-you",
                )
                if not success:
                    return False, f"跳转失败: {next_state}"
                state = next_state
                self.last_registration_state = state
                continue

            if (not register_submitted) and (not otp_verified) and (not account_created):
                self._log(f"未知起始状态，回退为全新注册流程: {describe_flow_state(state)}")
                state = self._state_from_url(f"{self.AUTH}/create-account/password")
                continue

            return False, f"未支持的注册状态: {describe_flow_state(state)}"

        return False, "注册状态机超出最大步数"
