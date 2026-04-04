"""
AnyAuto 专用 Sentinel Token 代理模块
转发至核心 OpenAI Sentinel 模块
"""

from ..openai.sentinel_token_v2 import build_sentinel_token, fetch_sentinel_challenge, SentinelTokenGenerator
