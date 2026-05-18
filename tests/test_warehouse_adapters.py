"""Tests for xft.warehouse.adapters — Prophet JSON to warehouse row conversion."""

from __future__ import annotations

from datetime import UTC, datetime

from xft.warehouse.adapters import (
    V1_EXPECTED_FILES,
    as_dict,
    as_list,
    build_bidding_summary_row,
    build_branch_rows,
    build_company_row,
    build_financing_event_rows,
    build_ip_summary_row,
    build_label_rows,
    build_outbound_investment_rows,
    build_profile_row,
    build_qualification_rows,
    build_recruitment_rows,
    build_risk_features_row,
    build_shareholder_rows,
    data,
    file_source,
    first_nonempty,
    get_path,
    import_status,
    ip_counts,
    json_text,
    parse_dt,
    risk_counts,
    to_float,
    to_int,
    _merge_shareholders,
)


def naive_dt(date_parts: tuple[int, int, int, int, int, int]) -> datetime:
    return datetime(*date_parts, tzinfo=UTC).replace(tzinfo=None)


class TestUtilityFunctions:
    """Tests for small helper functions."""

    def test_json_text_serializes_dict(self) -> None:
        assert json_text({"a": 1}) == '{"a": 1}'

    def test_json_text_sorts_keys(self) -> None:
        assert json_text({"z": 1, "a": 2}) == '{"a": 2, "z": 1}'

    def test_data_returns_payload(self) -> None:
        files = {"info.json": {"data": {"name": "Test"}}}
        assert data(files, "info.json") == {"name": "Test"}

    def test_data_returns_none_for_missing(self) -> None:
        assert data({}, "info.json") is None

    def test_as_dict_passes_through(self) -> None:
        assert as_dict({"a": 1}) == {"a": 1}

    def test_as_dict_defaults_to_empty(self) -> None:
        assert as_dict(None) == {}
        assert as_dict("string") == {}

    def test_as_list_passes_through(self) -> None:
        assert as_list([1, 2]) == [1, 2]

    def test_as_list_defaults_to_empty(self) -> None:
        assert as_list(None) == []
        assert as_list("string") == []

    def test_get_path_nested(self) -> None:
        payload = {"a": {"b": {"c": 42}}}
        assert get_path(payload, "a", "b", "c") == 42

    def test_get_path_returns_none_on_missing(self) -> None:
        assert get_path({}, "a", "b") is None

    def test_get_path_returns_none_on_non_dict(self) -> None:
        assert get_path("string", "a") is None

    def test_first_nonempty_picks_first_truthy(self) -> None:
        assert first_nonempty(None, "", [], {}, "winner", "ignored") == "winner"

    def test_first_nonempty_returns_none(self) -> None:
        assert first_nonempty(None, "", []) is None

    def test_to_int_parses_string(self) -> None:
        assert to_int("42") == 42

    def test_to_int_parses_float_string(self) -> None:
        assert to_int("3.14") == 3

    def test_to_int_returns_none_for_empty(self) -> None:
        assert to_int(None) is None
        assert to_int("") is None

    def test_to_int_returns_none_for_special_string(self) -> None:
        assert to_int("企业选择不公示") is None

    def test_to_float_parses_string(self) -> None:
        assert to_float("3.14") == 3.14

    def test_to_float_returns_none_for_empty(self) -> None:
        assert to_float(None) is None
        assert to_float("") is None

    def test_file_source_filters_existing(self) -> None:
        files = {"a.json": {}, "b.json": {}, "c.txt": {}}
        assert file_source(files, ["a.json", "b.json", "missing.json"]) == ["a.json", "b.json"]


class TestParseDt:
    """Tests for the flexible date parser."""

    def test_parse_dt_none(self) -> None:
        assert parse_dt(None) is None

    def test_parse_dt_empty(self) -> None:
        assert parse_dt("") is None

    def test_parse_dt_epoch_placeholder(self) -> None:
        assert parse_dt("1900-01-01") is None

    def test_parse_dt_timestamp_seconds(self) -> None:
        ts = datetime(2024, 1, 15, 8, 30, 0, tzinfo=UTC).timestamp()
        result = parse_dt(int(ts))
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_parse_dt_timestamp_milliseconds(self) -> None:
        ts = datetime(2024, 1, 15, 8, 30, 0, tzinfo=UTC).timestamp() * 1000
        result = parse_dt(int(ts))
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_parse_dt_iso_format(self) -> None:
        result = parse_dt("2024-03-20T10:30:00+00:00")
        assert result == naive_dt((2024, 3, 20, 10, 30, 0))

    def test_parse_dt_date_only(self) -> None:
        result = parse_dt("2024-03-20")
        assert result == naive_dt((2024, 3, 20, 0, 0, 0))

    def test_parse_dt_slash_format(self) -> None:
        result = parse_dt("2024/03/20")
        assert result == naive_dt((2024, 3, 20, 0, 0, 0))

    def test_parse_dt_datetime_string(self) -> None:
        result = parse_dt("2024-03-20 10:30:00")
        assert result == naive_dt((2024, 3, 20, 10, 30, 0))

    def test_parse_dt_invalid(self) -> None:
        assert parse_dt("not-a-date") is None


class TestImportStatus:
    """Tests for V1 file completeness check."""

    def test_complete(self) -> None:
        files = set(V1_EXPECTED_FILES)
        status, missing = import_status(files)
        assert status == "complete_or_rich"
        assert missing == []

    def test_partial(self) -> None:
        files = {"info.json", "label.json"}
        status, missing = import_status(files)
        assert status == "partial"
        assert len(missing) == len(V1_EXPECTED_FILES) - 2

    def test_meta_only(self) -> None:
        files = {".meta.json"}
        status, missing = import_status(files)
        assert status == "meta_only"


class TestBuildCompanyRow:
    """Tests for the core company row builder."""

    def _make_raw(self, **overrides: object) -> dict[str, dict[str, object]]:
        base: dict[str, dict[str, object]] = {
            "info.json": {"data": {"info": {"info": {"name": "测试公司", "regStatus": "存续"}}}},
            "query_company.json": {"data": {"entName": "查询名", "employeeNum": 100, "idtCtgNm": "制造业"}},
            "getbasinf.json": {"data": {"custNm": "基础名", "regCpt": "1000万"}},
            "business_scope.json": {"data": {"info": "经营范围"}},
            "ext.json": {"data": {"businessPhone": ["13800138000"], "website": "https://example.com"}},
        }
        base.update(overrides)
        return base

    def test_basic_fields(self) -> None:
        row = build_company_row("91330000MA1234567X", "测试公司", self._make_raw(), datetime.now(UTC))
        assert row["credit_code"] == "91330000MA1234567X"
        assert row["company_name"] == "测试公司"
        assert row["reg_status"] == "存续"
        assert row["employee_count"] == 100
        assert row["employee_count_source"] == "query_company"
        assert row["industry"] == "制造业"

    def test_employee_fallback_to_insurance(self) -> None:
        raw = self._make_raw()
        raw["insurances.json"] = {
            "data": [
                {"year": 2024, "people": 50},
                {"year": 2023, "people": 40},
            ]
        }
        # Remove employeeNum to trigger fallback
        raw["query_company.json"] = {"data": {"entName": "查询名"}}
        row = build_company_row("CC", "C", raw, datetime.now(UTC))
        assert row["employee_count"] == 50
        assert row["employee_count_source"] == "insurances"

    def test_phone_list_deduplication(self) -> None:
        raw = self._make_raw()
        raw["ext.json"] = {"data": {"businessPhone": ["13800138000", "13800138000"], "phones": ["13900139000"]}}
        row = build_company_row("CC", "C", raw, datetime.now(UTC))
        phones = row["phones"]
        assert "13800138000" in phones
        assert "13900139000" in phones

    def test_first_nonempty_chain(self) -> None:
        # info name is present, so query_company entName is ignored
        row = build_company_row("CC", "C", self._make_raw(), datetime.now(UTC))
        assert row["company_name"] == "测试公司"


class TestBuildLabelRows:
    """Tests for label extraction."""

    def test_simple_labels(self) -> None:
        raw = {"label.json": {"labels": ["高新", "专精特新"], "raw_label_codes": ["A", "B"]}}
        rows = build_label_rows("CC", raw)
        assert len(rows) == 2
        assert rows[0]["label_name"] == "高新"
        assert rows[0]["label_code"] == "A"
        assert rows[1]["label_name"] == "专精特新"
        assert rows[1]["label_code"] == "B"

    def test_query_base_labels(self) -> None:
        raw = {
            "query_base_label.json": {
                "data": [
                    {"labelName": "标签1", "labelClass": "类1", "labelType": 1},
                ]
            }
        }
        rows = build_label_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["label_name"] == "标签1"
        assert rows[0]["label_class"] == "类1"
        assert rows[0]["label_type"] == "1"


class TestBuildShareholderRows:
    """Tests for shareholder extraction from two sources."""

    def test_shareholder_json(self) -> None:
        raw = {"shareholder.json": {"data": {"list": [{"investorName": "张三", "subscribeAmount": 100}]}}}
        rows = build_shareholder_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["shareholder_name"] == "张三"
        assert rows[0]["subscribe_amount"] == "100"

    def test_equity_structure_json(self) -> None:
        raw = {"equity_structure.json": {"data": [{"name": "李四", "amomon": 200, "investmentRate": 0.5}]}}
        rows = build_shareholder_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["shareholder_name"] == "李四"
        assert rows[0]["subscribe_amount"] == "200"
        assert rows[0]["investment_rate"] == 0.5


class TestBuildIpSummaryRow:
    """Tests for IP summary builder."""

    def test_counts_from_intellectual(self) -> None:
        raw = {"intellectual.json": {"data": {"intellectual": [{"name": "专利查询", "messageNo": 10}]}}}
        row = build_ip_summary_row("CC", raw)
        assert row["patent_count"] == 10
        assert row["trademark_count"] == 0  # fallback to by_name returns 0

    def test_counts_from_summary_files(self) -> None:
        raw = {
            "brand.json": {"data": {"total": 5}},
            "partner.json": {"data": {"total": 3}},
        }
        row = build_ip_summary_row("CC", raw)
        assert row["trademark_count"] == 5
        assert row["patent_count"] == 3


class TestBuildRiskFeaturesRow:
    """Tests for risk feature builder."""

    def test_risk_counts(self) -> None:
        raw = {
            "risk_insight.json": {
                "data": {
                    "riskCount": {"selfRisk": 5, "preRisk": 3, "arroundRisk": 2},
                    "riskInsight": [{"category": "经营异常"}],
                }
            }
        }
        row = build_risk_features_row("CC", raw)
        assert row["self_risk_count"] == 5
        assert row["pre_risk_count"] == 3
        assert row["around_risk_count"] == 2


class TestBuildRecruitmentRows:
    """Tests for recruitment row builder."""

    def test_basic(self) -> None:
        raw = {
            "recruit_message.json": {"data": {"list": [{"title": "Java工程师", "city": "深圳", "oriSalary": "15-25K"}]}}
        }
        rows = build_recruitment_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["title"] == "Java工程师"
        assert rows[0]["salary_text"] == "15-25K"

    def test_skips_empty_title(self) -> None:
        raw = {"recruit_message.json": {"data": {"list": [{"title": "", "city": "深圳"}]}}}
        rows = build_recruitment_rows("CC", raw)
        assert len(rows) == 0


class TestBuildBiddingSummaryRow:
    """Tests for bidding summary builder."""

    def test_winner_and_inviting(self) -> None:
        raw = {
            "query_company_bidding_new_winner.json": {"total": 10, "list": [{"pubTime": "2024-01"}]},
            "query_company_bidding_new_inviting.json": {"total": 5, "list": []},
            "query_bidding_total.json": {"data": {"total": 20}},
        }
        row = build_bidding_summary_row("CC", raw)
        assert row["winner_count"] == 10
        assert row["inviting_count"] == 5
        assert row["total_count"] == 20


class TestBuildQualificationRows:
    """Tests for qualification row builder."""

    def test_query_qualification(self) -> None:
        raw = {
            "query_qualification.json": {"data": [{"labNm": "ISO9001", "ancNm": "质量管理", "pblhDt": "2024-01-15"}]}
        }
        rows = build_qualification_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["qualification_name"] == "ISO9001"
        assert rows[0]["qualification_type"] == "质量管理"

    def test_certification_json(self) -> None:
        raw = {
            "certification.json": {"data": {"list": [{"certificateName": "高新技术企业", "startDate": "2023-06-01"}]}}
        }
        rows = build_qualification_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["qualification_name"] == "高新技术企业"
        assert rows[0]["source_file"] == "certification.json"


class TestBuildBranchRows:
    """Tests for branch row builder."""

    def test_basic(self) -> None:
        raw = {"branch.json": {"data": {"list": [{"name": "深圳分公司", "regStatus": "存续"}]}}}
        rows = build_branch_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["branch_name"] == "深圳分公司"


class TestBuildFinancingEventRows:
    """Tests for financing event builder."""

    def test_basic(self) -> None:
        raw = {
            "query_investment_event_new.json": {
                "data": {"list": [{"releaseDate": "2024-01-15", "financingRounds": "A轮", "financingAmount": "1亿"}]}
            }
        }
        rows = build_financing_event_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["financing_round"] == "A轮"


class TestBuildOutboundInvestmentRows:
    """Tests for outbound investment builder."""

    def test_basic(self) -> None:
        raw = {
            "query_investor.json": {"data": {"list": [{"companyName": "子公司A", "amount": 500, "regStatus": "存续"}]}}
        }
        rows = build_outbound_investment_rows("CC", raw)
        assert len(rows) == 1
        assert rows[0]["invested_company_name"] == "子公司A"
        assert rows[0]["amount"] == 500.0


class TestMergeShareholders:
    """Tests for shareholder merge logic."""

    def test_merge_by_name(self) -> None:
        rows = [
            {"shareholder_name": "张三", "subscribe_amount": "100", "paid_amount": None, "investment_rate": None},
            {"shareholder_name": "张三", "subscribe_amount": None, "paid_amount": "80", "investment_rate": 0.2},
        ]
        merged = _merge_shareholders(rows)
        assert len(merged) == 1
        assert merged[0]["subscribe_amount"] == "100"
        assert merged[0]["paid_amount"] == "80"
        assert merged[0]["investment_rate"] == 0.2

    def test_skips_empty_name(self) -> None:
        rows = [{"shareholder_name": None, "subscribe_amount": "100"}]
        merged = _merge_shareholders(rows)
        assert len(merged) == 0


class TestBuildProfileRow:
    """Tests for the wide profile row builder."""

    def test_basic(self) -> None:
        company = {
            "credit_code": "CC",
            "company_name": "测试公司",
            "industry": "制造业",
            "employee_count": 100,
            "business_scope": "经营范围",
        }
        row = build_profile_row(
            company=company,
            labels=[{"label_name": "高新"}],
            ip={"trademark_count": 5, "patent_count": 3},
            risk={"self_risk_count": 1},
            recruitments=[{"title": "工程师"}],
            bidding={"total_count": 10},
            branches=[{"branch_name": "分公司"}],
            shareholders=[{"shareholder_name": "张三"}],
            qualifications=[{"qualification_name": "ISO"}],
            financing_events=[{"financing_round": "A轮"}],
            outbound_investments=[{"invested_company_name": "子公司"}],
            missing_v1_files=[],
            import_status_value="complete_or_rich",
            raw_files={"info.json": {}},
            updated_at=datetime.now(UTC),
        )
        assert row["credit_code"] == "CC"
        assert row["company_name"] == "测试公司"
        assert row["is_listed"] is False
        assert row["recruitment_count"] == 1
        assert row["branch_count"] == 1
        assert row["qualification_count"] == 1
        assert row["financing_event_count"] == 1
        assert row["outbound_investment_count"] == 1
        assert row["import_status"] == "complete_or_rich"
        assert 0 < row["profile_completeness"] <= 1

    def test_profile_completeness_required_only(self) -> None:
        company = {
            "credit_code": "CC",
            "company_name": "测试公司",
            "industry": "制造业",
            "employee_count": 100,
            "business_scope": "经营范围",
        }
        row = build_profile_row(
            company=company,
            labels=[],
            ip={},
            risk={},
            recruitments=[],
            bidding={},
            branches=[],
            shareholders=[],
            qualifications=[],
            financing_events=[],
            outbound_investments=[],
            missing_v1_files=[],
            import_status_value="complete_or_rich",
            raw_files={},
            updated_at=datetime.now(UTC),
        )
        # All 5 required fields present → 0.65 base
        # ip_counts({}) and risk_counts({}) return non-empty dicts (all zeros)
        # so 2/7 recommended fields are truthy → +0.1
        assert row["profile_completeness"] == 0.75

    def test_is_listed_true(self) -> None:
        company = {
            "credit_code": "CC",
            "company_name": "测试公司",
            "industry": "制造业",
            "employee_count": 100,
            "business_scope": "经营范围",
            "stock_code": "600001",
        }
        row = build_profile_row(
            company=company,
            labels=[],
            ip={},
            risk={},
            recruitments=[],
            bidding={},
            branches=[],
            shareholders=[],
            qualifications=[],
            financing_events=[],
            outbound_investments=[],
            missing_v1_files=[],
            import_status_value="complete_or_rich",
            raw_files={},
            updated_at=datetime.now(UTC),
        )
        assert row["is_listed"] is True


class TestIpCounts:
    """Tests for ip_counts helper."""

    def test_basic(self) -> None:
        result = ip_counts({"trademark_count": 5, "patent_count": 3})
        assert result["trademark"] == 5
        assert result["patent"] == 3
        assert result["software"] == 0


class TestRiskCounts:
    """Tests for risk_counts helper."""

    def test_basic(self) -> None:
        result = risk_counts({"self_risk_count": 1, "pre_risk_count": 2})
        assert result["self"] == 1
        assert result["pre"] == 2
        assert result["around"] == 0
