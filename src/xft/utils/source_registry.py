"""source_registry: classify web sources by type and authority level.

Lightweight, pure-function module. First version only identifies and labels
sources — does not make conflict-resolution decisions.

Usage:
    from xft.utils.source_registry import classify_source
    info = classify_source("https://www.qcc.com/...")
    # info.source_type == "commercial_registry"
    # info.authority_level == "high"
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel

SourceType = Literal[
    "government_registry",
    "official_ip",
    "government_notice",
    "commercial_registry",
    "company_website",
    "recruiting",
    "b2b_marketplace",
    "news",
    "map_directory",
    "search_ai",
    "unknown",
]

AuthorityLevel = Literal["high", "medium", "low", "unknown"]


class SourceInfo(BaseModel):
    source_type: SourceType
    authority_level: AuthorityLevel
    display_name: str
    domain: str | None = None
    should_fetch_bias: Literal["prefer", "neutral", "avoid"] = "neutral"
    notes: str = ""


_DOMAIN_RULES: list[tuple[str, SourceInfo]] = [
    (
        "gsxt.gov.cn",
        SourceInfo(
            source_type="government_registry",
            authority_level="high",
            display_name="国家企业信用信息公示系统",
            should_fetch_bias="prefer",
        ),
    ),
    (
        "cnipa.gov.cn",
        SourceInfo(
            source_type="official_ip",
            authority_level="high",
            display_name="国家知识产权局",
            should_fetch_bias="prefer",
        ),
    ),
    (
        "qcc.com",
        SourceInfo(
            source_type="commercial_registry",
            authority_level="high",
            display_name="企查查",
            should_fetch_bias="avoid",
            notes="商业工商库，详情页可能登录受限",
        ),
    ),
    (
        "tianyancha.com",
        SourceInfo(
            source_type="commercial_registry",
            authority_level="high",
            display_name="天眼查",
            should_fetch_bias="avoid",
        ),
    ),
    (
        "aiqicha.baidu.com",
        SourceInfo(
            source_type="commercial_registry",
            authority_level="high",
            display_name="爱企查",
            should_fetch_bias="avoid",
        ),
    ),
    (
        "qixin.com",
        SourceInfo(
            source_type="commercial_registry",
            authority_level="high",
            display_name="企信宝",
            should_fetch_bias="avoid",
        ),
    ),
    (
        "zhipin.com",
        SourceInfo(
            source_type="recruiting",
            authority_level="medium",
            display_name="BOSS直聘",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "liepin.com",
        SourceInfo(
            source_type="recruiting",
            authority_level="medium",
            display_name="猎聘",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "51job.com",
        SourceInfo(
            source_type="recruiting",
            authority_level="medium",
            display_name="前程无忧",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "1688.com",
        SourceInfo(
            source_type="b2b_marketplace",
            authority_level="medium",
            display_name="1688",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "dianping.com",
        SourceInfo(
            source_type="map_directory",
            authority_level="low",
            display_name="大众点评",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "baidu.com/map",
        SourceInfo(
            source_type="map_directory",
            authority_level="low",
            display_name="百度地图",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "map.baidu.com",
        SourceInfo(
            source_type="map_directory",
            authority_level="low",
            display_name="百度地图",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "amap.com",
        SourceInfo(
            source_type="map_directory",
            authority_level="low",
            display_name="高德地图",
            should_fetch_bias="neutral",
        ),
    ),
    (
        "metaso.cn",
        SourceInfo(
            source_type="search_ai",
            authority_level="medium",
            display_name="秘塔AI搜索",
            should_fetch_bias="avoid",
        ),
    ),
]


def classify_source(url: str | None, title: str = "") -> SourceInfo:
    """Classify a URL into source type, authority level, and display name.

    Args:
        url: Full URL or None.  None / empty strings return unknown.
        title: Optional page title, used as fallback display_name for unknowns.

    Returns:
        SourceInfo with the best-match classification.
    """
    if not url:
        return SourceInfo(
            source_type="unknown",
            authority_level="unknown",
            display_name=title or "未知来源",
        )

    if url.startswith("metaso://"):
        return SourceInfo(
            source_type="search_ai",
            authority_level="medium",
            display_name="秘塔AI搜索",
            domain="metaso://",
            should_fetch_bias="avoid",
        )

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    host = host.removeprefix("www.")
    path = parsed.path

    for suffix, info in _DOMAIN_RULES:
        if "/" in suffix:
            # Path-based rule: match host+path starts with suffix
            if host + path == suffix or (host + path).startswith(suffix):
                return info.model_copy(update={"domain": host})
        elif host == suffix or host.endswith("." + suffix):
            return info.model_copy(update={"domain": host})

    if host.endswith(".gov.cn"):
        return SourceInfo(
            source_type="government_notice",
            authority_level="high",
            display_name="政府网站",
            domain=host,
            should_fetch_bias="prefer",
        )

    return SourceInfo(
        source_type="unknown",
        authority_level="unknown",
        display_name=host or title or "未知来源",
        domain=host or None,
    )
