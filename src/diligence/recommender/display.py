"""Display helpers for recommender reports and evidence strings."""

from __future__ import annotations

from typing import Any

FIELD_VALUE_LABELS: dict[str, dict[str, str]] = {
    "risk_counts": {
        "self": "自身风险",
        "pre": "历史变更风险",
        "around": "周边风险",
        "court_session": "开庭公告",
        "judgement_doc": "裁判文书",
        "announcement": "公告信息",
        "inspection": "抽查检查",
        "change": "工商变更",
    },
    "ip_counts": {
        "trademark": "商标",
        "patent": "专利",
        "software": "软件著作权",
        "works_copyright": "作品著作权",
        "website_filing": "网站备案",
        "product": "产品信息",
    },
    "bank_flags": {
        "high_quality_customer": "高质量客户",
        "credit_granting_customer": "授信客户",
        "china_finance": "中银金融标签",
        "high_operation_value_customer": "高经营价值客户",
    },
    "cross_border_flags": {
        "small_export": "小微出口",
        "service_trade": "服务贸易",
        "small_service_trade": "小微服务贸易",
        "labels": "跨境标签",
    },
}


def label_for_value_key(field: str, key: str) -> str:
    """Return a human-readable label for a nested profile value key."""
    return FIELD_VALUE_LABELS.get(field, {}).get(key, key)


def format_profile_value(value: Any, *, field: str = "") -> str:
    """Format a profile value for human-facing evidence/report output."""
    if isinstance(value, dict):
        nonzero = {k: v for k, v in value.items() if _has_display_value(v)}
        parts = [
            f"{label_for_value_key(field, str(key))}:{format_profile_value(val, field=field)}"
            for key, val in list(nonzero.items())[:8]
        ]
        return "、".join(parts) or "无明显记录"
    if isinstance(value, list):
        return "、".join(str(item) for item in value[:8] if _has_display_value(item)) or "无明显记录"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _has_display_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return True

