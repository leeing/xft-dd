from __future__ import annotations

from diligence.utils.source_registry import classify_source


def test_classify_empty_url() -> None:
    result = classify_source(None)
    assert result.source_type == "unknown"
    assert result.authority_level == "unknown"
    assert result.display_name == "未知来源"


def test_classify_empty_url_with_title() -> None:
    result = classify_source(None, title="某公司简介")
    assert result.display_name == "某公司简介"


def test_classify_gsxt_high() -> None:
    result = classify_source("https://www.gsxt.gov.cn/index.html")
    assert result.source_type == "government_registry"
    assert result.authority_level == "high"
    assert result.display_name == "国家企业信用信息公示系统"
    assert result.should_fetch_bias == "prefer"


def test_classify_gsxt_subdomain() -> None:
    result = classify_source("http://sh.gsxt.gov.cn/search")
    assert result.source_type == "government_registry"
    assert result.authority_level == "high"


def test_classify_cnipa_high() -> None:
    result = classify_source("https://cnipa.gov.cn/patent/123")
    assert result.source_type == "official_ip"
    assert result.authority_level == "high"
    assert result.should_fetch_bias == "prefer"


def test_classify_qcc_commercial_avoid() -> None:
    result = classify_source("https://www.qcc.com/company/123")
    assert result.source_type == "commercial_registry"
    assert result.authority_level == "high"
    assert result.display_name == "企查查"
    assert result.should_fetch_bias == "avoid"


def test_classify_tianyancha() -> None:
    result = classify_source("https://www.tianyancha.com/company/456")
    assert result.source_type == "commercial_registry"
    assert result.display_name == "天眼查"
    assert result.should_fetch_bias == "avoid"


def test_classify_zhipin() -> None:
    result = classify_source("https://www.zhipin.com/job_detail/abc")
    assert result.source_type == "recruiting"
    assert result.authority_level == "medium"
    assert result.should_fetch_bias == "neutral"


def test_classify_1688() -> None:
    result = classify_source("https://detail.1688.com/offer/123")
    assert result.source_type == "b2b_marketplace"
    assert result.authority_level == "medium"


def test_classify_metaso() -> None:
    result = classify_source("metaso://search?q=某公司")
    assert result.source_type == "search_ai"
    assert result.display_name == "秘塔AI搜索"
    assert result.should_fetch_bias == "avoid"


def test_classify_unknown() -> None:
    result = classify_source("https://some-random-blog.com/post/1")
    assert result.source_type == "unknown"
    assert result.authority_level == "unknown"
    assert result.domain == "some-random-blog.com"


def test_classify_gov_cn_fallback() -> None:
    result = classify_source("https://scjgj.sh.gov.cn/notice/1")
    assert result.source_type == "government_notice"
    assert result.authority_level == "high"
    assert result.should_fetch_bias == "prefer"
    assert result.display_name == "政府网站"


def test_classify_www_prefix_stripped() -> None:
    result = classify_source("https://www.baidu.com/map/place/xyz")
    assert result.source_type == "map_directory"
    assert result.authority_level == "low"


def test_classify_map_baidu_subdomain() -> None:
    result = classify_source("https://map.baidu.com/place/abc")
    assert result.source_type == "map_directory"
    assert result.display_name == "百度地图"
