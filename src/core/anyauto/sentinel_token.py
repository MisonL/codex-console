"""
AnyAuto 专用 Sentinel Token 代理模块
转发至核心 OpenAI Sentinel 模块
"""

from ..openai.sentinel_headers import (
    build_sentinel_token,
    build_sentinel_token_async,
    fetch_sentinel_challenge,
    fetch_sentinel_challenge_async,
)
