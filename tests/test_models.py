from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from diligence.models import (
    BatchRunMeta,
    CompanyRunResult,
    DimensionSearchResult,
    RunMeta,
    SearchItem,
    make_item_id,
)


def test_make_item_id_with_url() -> None:
    url = "https://qcc.com/company/123"
    result = make_item_id(url=url, title="t", snippet="s")
    expected = hashlib.sha1(url.encode()).hexdigest()[:12]
    assert result == expected


def test_make_item_id_without_url() -> None:
    result = make_item_id(url=None, title="企业名", snippet="注册资本100万")
    expected = hashlib.sha1(("企业名" + "注册资本100万").encode()).hexdigest()[:12]
    assert result == expected


def test_make_item_id_url_takes_priority() -> None:
    id1 = make_item_id(url="https://example.com", title="t", snippet="s")
    id2 = make_item_id(url="https://example.com", title="different", snippet="different")
    assert id1 == id2


def test_dimension_search_result_status_literals() -> None:
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=[],
    )
    assert dsr.status == "success"


def test_run_meta_defaults() -> None:
    rm = RunMeta(
        run_id="20260509-120000-abc123",
        target="某公司",
        started_at=datetime.now(timezone.utc),
        status="success",
        config_path="config.yaml",
        active_dimensions=["basic_info"],
    )
    assert rm.required_failed is False
    assert rm.failed_dimensions == []


def test_company_run_result_skipped() -> None:
    r = CompanyRunResult(index=1, target="某公司", status="skipped")
    assert r.run_id is None
    assert r.report_path is None


def test_batch_run_meta_index_target_map() -> None:
    bm = BatchRunMeta(
        batch_id="20260509-160000-batch-f3a1b2",
        index_target_map={1: "公司A", 2: "公司B"},
        total=2,
        success=1,
        partial=0,
        failed=1,
        skipped=0,
        started_at=datetime.now(timezone.utc),
        config_path="config.yaml",
    )
    assert bm.index_target_map[1] == "公司A"
