"""Adapters from Prophet JSON shapes to warehouse rows."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

JsonDict = dict[str, Any]
RawFiles = dict[str, JsonDict]
EPOCH_MILLISECONDS_THRESHOLD = 10_000_000_000


V1_EXPECTED_FILES: tuple[str, ...] = (
    "info.json",
    "query_company.json",
    "label.json",
    "intellectual.json",
    "risk_insight.json",
    "recruit_message.json",
    "query_bidding_total.json",
    "query_qualification.json",
    "staff.json",
    "shareholder.json",
)


def json_text(value: Any) -> str:
    """Serialize a value for DuckDB JSON columns."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def data(raw_files: RawFiles, filename: str) -> Any:
    """Return the top-level data payload for a source file."""
    raw = raw_files.get(filename)
    return raw.get("data") if isinstance(raw, dict) else None


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def get_path(value: Any, *path: str) -> Any:
    cur = value
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def to_int(value: Any) -> int | None:
    if value in (None, "", "企业选择不公示"):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def parse_dt(value: Any) -> datetime | None:
    """Parse common Prophet date formats into naive datetimes for DuckDB."""
    if value in (None, "", "1900-01-01"):
        return None
    if isinstance(value, int | float):
        ts = float(value)
        if ts > EPOCH_MILLISECONDS_THRESHOLD:
            ts /= 1000
        return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19], fmt)  # noqa: DTZ007
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def file_source(files: RawFiles, candidates: Iterable[str]) -> list[str]:
    return [name for name in candidates if name in files]


def import_status(json_files: set[str]) -> tuple[str, list[str]]:
    missing = [name for name in V1_EXPECTED_FILES if name not in json_files]
    non_meta = [name for name in json_files if name != ".meta.json"]
    if not non_meta:
        return "meta_only", missing
    if missing:
        return "partial", missing
    return "complete_or_rich", []


def build_company_row(credit_code: str, company_name: str, raw_files: RawFiles, updated_at: datetime) -> JsonDict:
    info_base = as_dict(get_path(data(raw_files, "info.json"), "info", "info"))
    query = as_dict(data(raw_files, "query_company.json"))
    getbasinf = as_dict(data(raw_files, "getbasinf.json"))
    business_scope = as_dict(data(raw_files, "business_scope.json"))
    ext = as_dict(data(raw_files, "ext.json"))
    insurance_rows = as_list(data(raw_files, "insurances.json"))

    employee_count = first_nonempty(query.get("employeeNum"), query.get("empNum"))
    employee_source = "query_company" if (employee_count is not None and to_int(employee_count) != 0) else None
    if employee_count is None or to_int(employee_count) == 0:
        latest_insurance = max(
            (row for row in insurance_rows if isinstance(row, dict) and to_int(row.get("people")) is not None),
            key=lambda row: to_int(row.get("year")) or 0,
            default=None,
        )
        if latest_insurance:
            employee_count = latest_insurance.get("people")
            employee_source = "insurances"

    phones: list[str] = []
    for value in (ext.get("businessPhone"), ext.get("phones"), query.get("phoneList")):
        phones.extend(str(item) for item in as_list(value) if item)

    source_files = file_source(
        raw_files,
        ("info.json", "query_company.json", "getbasinf.json", "business_scope.json", "ext.json", "insurances.json"),
    )
    return {
        "credit_code": credit_code,
        "company_name": first_nonempty(
            info_base.get("name"),
            query.get("entName"),
            getbasinf.get("custNm"),
            company_name,
        ),
        "industry": first_nonempty(query.get("idtCtgNm"), info_base.get("cate1")),
        "industry_big": first_nonempty(query.get("industryBig"), info_base.get("cate2")),
        "industry_mid": info_base.get("cate3"),
        "industry_small": query.get("idtSmlClsNm"),
        "employee_count": to_int(employee_count),
        "employee_count_source": employee_source,
        "registered_capital": first_nonempty(info_base.get("regCapital"), getbasinf.get("regCpt")),
        "registered_capital_currency": query.get("regCapCur"),
        "registered_location": first_nonempty(info_base.get("regLocation"), getbasinf.get("ofcAdr")),
        "province": query.get("province"),
        "county": query.get("county"),
        "business_scope": first_nonempty(
            info_base.get("businessScope"),
            business_scope.get("info"),
            getbasinf.get("mainBus"),
        ),
        "established_at": parse_dt(
            first_nonempty(info_base.get("estiblishTime"), query.get("establishDate"), getbasinf.get("foundDt"))
        ),
        "legal_person": first_nonempty(
            info_base.get("legalPersonName"),
            query.get("legalName"),
            getbasinf.get("lglRprsPsn"),
        ),
        "reg_status": info_base.get("regStatus"),
        "company_org_type": info_base.get("companyOrgType"),
        "listed_company_state": to_int(info_base.get("listedCompanyState")),
        "stock_code": first_nonempty(getbasinf.get("stkCd"), info_base.get("secucode")),
        "stock_short_name": first_nonempty(getbasinf.get("scrShtNm"), info_base.get("secuabbr")),
        "website": first_nonempty(ext.get("website"), getbasinf.get("cmpHmpg")),
        "email": first_nonempty(ext.get("email"), *(as_list(query.get("emailList"))[:1])),
        "phones": json_text(sorted(set(phones))),
        "source_files": json_text(source_files),
        "raw_refs": json_text({name: name for name in source_files}),
        "updated_at": updated_at,
    }


def build_label_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows: list[JsonDict] = []
    label = raw_files.get("label.json", {})
    labels = as_list(label.get("labels"))
    codes = as_list(label.get("raw_label_codes"))
    for idx, label_name in enumerate(labels):
        rows.append(
            {
                "credit_code": credit_code,
                "label_name": str(label_name),
                "label_code": str(codes[idx]) if idx < len(codes) else None,
                "label_class": None,
                "label_type": None,
                "source_file": "label.json",
                "raw_json": json_text({"label": label_name, "code": codes[idx] if idx < len(codes) else None}),
            }
        )

    for item in as_list(data(raw_files, "query_base_label.json")):
        if not isinstance(item, dict):
            continue
        name = item.get("labelName")
        if not name:
            continue
        rows.append(
            {
                "credit_code": credit_code,
                "label_name": str(name),
                "label_code": None,
                "label_class": item.get("labelClass"),
                "label_type": str(item.get("labelType")) if item.get("labelType") is not None else None,
                "source_file": "query_base_label.json",
                "raw_json": json_text(item),
            }
        )
    return rows


def build_key_personnel_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    affiliate_by_name = {
        str(item.get("name")): to_int(item.get("affiliateCompany"))
        for item in as_list(get_path(data(raw_files, "slow.json"), "staffExts"))
        if isinstance(item, dict) and item.get("name")
    }
    rows = []
    for item in as_list(get_path(data(raw_files, "staff.json"), "list")):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        rows.append(
            {
                "credit_code": credit_code,
                "person_name": item.get("name"),
                "role": item.get("staffTypeName"),
                "affiliate_company_count": affiliate_by_name.get(str(item.get("name"))),
                "source_file": "staff.json",
                "raw_json": json_text(item),
            }
        )
    return rows


def build_shareholder_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows: list[JsonDict] = [
        {
            "credit_code": credit_code,
            "shareholder_name": item.get("investorName"),
            "subscribe_amount": str(item.get("subscribeAmount")) if item.get("subscribeAmount") else None,
            "paid_amount": str(item.get("paidAmount")) if item.get("paidAmount") else None,
            "investment_rate": None,
            "proportion": None,
            "investor_type": None,
            "listed": None,
            "source_file": "shareholder.json",
            "raw_json": json_text(item),
        }
        for item in as_list(get_path(data(raw_files, "shareholder.json"), "list"))
        if isinstance(item, dict) and item.get("investorName")
    ]
    rows.extend(
        {
            "credit_code": credit_code,
            "shareholder_name": item.get("name"),
            "subscribe_amount": str(item.get("amomon")) if item.get("amomon") is not None else None,
            "paid_amount": None,
            "investment_rate": to_float(item.get("investmentRate")),
            "proportion": str(item.get("pCTOFTOTALSHARES")) if item.get("pCTOFTOTALSHARES") else None,
            "investor_type": str(item.get("investorType")) if item.get("investorType") is not None else None,
            "listed": item.get("listed") if isinstance(item.get("listed"), bool) else None,
            "source_file": "equity_structure.json",
            "raw_json": json_text(item),
        }
        for item in as_list(data(raw_files, "equity_structure.json"))
        if isinstance(item, dict) and item.get("name")
    )
    return rows


def _summary_count(raw_files: RawFiles, filename: str, fallback_path: tuple[str, ...]) -> int:
    payload = data(raw_files, filename)
    if isinstance(payload, dict) and to_int(payload.get("total")) is not None:
        return to_int(payload.get("total")) or 0
    if isinstance(payload, dict) and isinstance(payload.get("list"), list):
        return len(payload["list"])
    value = get_path(payload, *fallback_path) if fallback_path else None
    return to_int(value) or 0


def build_ip_summary_row(credit_code: str, raw_files: RawFiles) -> JsonDict:
    intellectual = as_list(get_path(data(raw_files, "intellectual.json"), "intellectual"))

    def by_name(name: str) -> int:
        for item in intellectual:
            if isinstance(item, dict) and item.get("name") == name:
                return to_int(item.get("messageNo")) or 0
        return 0

    patents = as_list(get_path(data(raw_files, "partner.json"), "list"))
    trademarks = as_list(get_path(data(raw_files, "brand.json"), "list"))
    source_files = file_source(
        raw_files,
        (
            "intellectual.json",
            "brand.json",
            "partner.json",
            "software.json",
            "copyright.json",
            "products.json",
            "record.json",
        ),
    )
    return {
        "credit_code": credit_code,
        "trademark_count": _summary_count(raw_files, "brand.json", ()) or by_name("商标查询"),
        "patent_count": _summary_count(raw_files, "partner.json", ()) or by_name("专利查询"),
        "software_count": _summary_count(raw_files, "software.json", ()) or by_name("软件著作权"),
        "works_copyright_count": _summary_count(raw_files, "copyright.json", ()) or by_name("作品著作权"),
        "website_filing_count": _summary_count(raw_files, "record.json", ()) or by_name("网站备案"),
        "product_count": _summary_count(raw_files, "products.json", ()),
        "latest_patent_titles": json_text([item.get("title") for item in patents[:5] if isinstance(item, dict)]),
        "latest_trademark_names": json_text([item.get("tmName") for item in trademarks[:5] if isinstance(item, dict)]),
        "source_files": json_text(source_files),
        "raw_refs": json_text({name: name for name in source_files}),
    }


def build_risk_features_row(credit_code: str, raw_files: RawFiles) -> JsonDict:
    risk_count = as_dict(get_path(data(raw_files, "risk_insight.json"), "riskCount"))
    risk_insight = as_list(get_path(data(raw_files, "risk_insight.json"), "riskInsight"))
    source_files = file_source(
        raw_files,
        (
            "risk_insight.json",
            "business_info.json",
            "judgement_doc.json",
            "annoucement.json",
            "check.json",
            "change.json",
        ),
    )
    return {
        "credit_code": credit_code,
        "self_risk_count": to_int(risk_count.get("selfRisk")) or 0,
        "pre_risk_count": to_int(risk_count.get("preRisk")) or 0,
        "around_risk_count": to_int(risk_count.get("arroundRisk")) or 0,
        "court_session_count": _summary_count(raw_files, "business_info.json", ()),
        "judgement_doc_count": _summary_count(raw_files, "judgement_doc.json", ()),
        "announcement_count": _summary_count(raw_files, "annoucement.json", ()),
        "inspection_count": _summary_count(raw_files, "check.json", ()),
        "change_count": _summary_count(raw_files, "change.json", ()),
        "risk_categories": json_text(risk_insight),
        "source_files": json_text(source_files),
        "raw_refs": json_text({name: name for name in source_files}),
    }


def build_recruitment_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows = []
    for item in as_list(get_path(data(raw_files, "recruit_message.json"), "list")):
        if not isinstance(item, dict) or not item.get("title"):
            continue
        rows.append(
            {
                "credit_code": credit_code,
                "title": item.get("title"),
                "city": item.get("city"),
                "district": item.get("district"),
                "education": item.get("education"),
                "experience": item.get("experience"),
                "salary_text": item.get("oriSalary"),
                "employer_number": item.get("employerNumber"),
                "source": item.get("source"),
                "start_date": parse_dt(item.get("startdate")),
                "end_date": parse_dt(item.get("enddate")),
                "raw_json": json_text(item),
            }
        )
    return rows


def build_bidding_summary_row(credit_code: str, raw_files: RawFiles) -> JsonDict:
    winner_items = as_list(raw_files.get("query_company_bidding_new_winner.json", {}).get("list"))
    inviting_items = as_list(raw_files.get("query_company_bidding_new_inviting.json", {}).get("list"))
    latest = sorted(
        [item for item in [*winner_items, *inviting_items] if isinstance(item, dict)],
        key=lambda item: str(first_nonempty(item.get("pubTime"), item.get("sortValue"), "")),
        reverse=True,
    )[:5]
    return {
        "credit_code": credit_code,
        "total_count": to_int(get_path(data(raw_files, "query_bidding_total.json"), "total")) or len(latest),
        "winner_count": to_int(raw_files.get("query_company_bidding_new_winner.json", {}).get("total"))
        or len(winner_items),
        "inviting_count": to_int(raw_files.get("query_company_bidding_new_inviting.json", {}).get("total"))
        or len(inviting_items),
        "latest_items": json_text(latest),
        "source_files": json_text(
            file_source(
                raw_files,
                (
                    "query_bidding_total.json",
                    "query_company_bidding_new_winner.json",
                    "query_company_bidding_new_inviting.json",
                ),
            )
        ),
    }


def build_qualification_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows = [
        {
            "credit_code": credit_code,
            "qualification_name": item.get("labNm"),
            "qualification_type": item.get("ancNm"),
            "level_name": item.get("lvlNm"),
            "publish_date": parse_dt(item.get("pblhDt")),
            "valid_from": parse_dt(item.get("eftDt")),
            "valid_to": parse_dt(item.get("nvldDt")),
            "issuing_org": item.get("revOrPblhOrg"),
            "source_file": "query_qualification.json",
            "raw_json": json_text(item),
        }
        for item in as_list(data(raw_files, "query_qualification.json"))
        if isinstance(item, dict) and item.get("labNm")
    ]
    rows.extend(
        {
            "credit_code": credit_code,
            "qualification_name": item.get("certificateName"),
            "qualification_type": "certification",
            "level_name": None,
            "publish_date": None,
            "valid_from": parse_dt(item.get("startDate")),
            "valid_to": parse_dt(item.get("endDate")),
            "issuing_org": None,
            "source_file": "certification.json",
            "raw_json": json_text(item),
        }
        for item in as_list(get_path(data(raw_files, "certification.json"), "list"))
        if isinstance(item, dict) and item.get("certificateName")
    )
    return rows


def build_branch_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows = []
    for item in as_list(get_path(data(raw_files, "branch.json"), "list")):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        rows.append(
            {
                "credit_code": credit_code,
                "branch_name": item.get("name"),
                "branch_credit_code": item.get("unifiedSocialCreditCode"),
                "reg_status": item.get("regStatus"),
                "legal_person": item.get("legalPersonName"),
                "established_at": parse_dt(item.get("estiblishTime")),
                "raw_json": json_text(item),
            }
        )
    return rows


def build_financing_event_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows = []
    for filename in ("query_investment_event_new.json", "query_fc_new.json"):
        for item in as_list(get_path(data(raw_files, filename), "list")):
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "credit_code": credit_code,
                    "event_date": parse_dt(item.get("releaseDate")),
                    "financing_round": item.get("financingRounds"),
                    "financing_amount": item.get("financingAmount"),
                    "product": item.get("financingProduct"),
                    "title": item.get("financingTitle"),
                    "source_file": filename,
                    "raw_json": json_text(item),
                }
            )
    return rows


def build_outbound_investment_rows(credit_code: str, raw_files: RawFiles) -> list[JsonDict]:
    rows = []
    for item in as_list(get_path(data(raw_files, "query_investor.json"), "list")):
        if not isinstance(item, dict) or not item.get("companyName"):
            continue
        rows.append(
            {
                "credit_code": credit_code,
                "invested_company_name": item.get("companyName"),
                "amount": to_float(item.get("amount")),
                "proportion": item.get("putCapitalProportion") or str(item.get("proportion") or ""),
                "reg_status": item.get("regStatus"),
                "source_file": "query_investor.json",
                "raw_json": json_text(item),
            }
        )
    return rows


def _merge_shareholders(rows: list[JsonDict]) -> list[JsonDict]:
    """Merge shareholder rows from shareholder.json and equity_structure.json by name.

    Prefers equity_structure.json for investment_rate and shareholder.json for paid_amount.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("shareholder_name") or "")
        if not name:
            continue
        if name not in merged:
            merged[name] = {
                "name": name,
                "subscribe_amount": None,
                "paid_amount": None,
                "investment_rate": None,
            }
        entry = merged[name]
        if row.get("subscribe_amount") is not None:
            entry["subscribe_amount"] = row["subscribe_amount"]
        if row.get("paid_amount") is not None:
            entry["paid_amount"] = row["paid_amount"]
        if row.get("investment_rate") is not None:
            entry["investment_rate"] = row["investment_rate"]
    return list(merged.values())


def build_profile_row(  # noqa: PLR0913
    *,
    company: JsonDict,
    labels: list[JsonDict],
    ip: JsonDict,
    risk: JsonDict,
    recruitments: list[JsonDict],
    bidding: JsonDict,
    branches: list[JsonDict],
    shareholders: list[JsonDict],
    qualifications: list[JsonDict],
    financing_events: list[JsonDict],
    outbound_investments: list[JsonDict],
    missing_v1_files: list[str],
    import_status_value: str,
    raw_files: RawFiles,
    updated_at: datetime,
) -> JsonDict:
    label_names = sorted({str(row["label_name"]) for row in labels})
    raw_label_codes = sorted({str(row["label_code"]) for row in labels if row.get("label_code")})
    query = as_dict(data(raw_files, "query_company.json"))
    bank_flags = {
        "high_quality_customer": query.get("highQualityCustomer") == "Y",
        "credit_granting_customer": query.get("creditGrantingCustomer") == "Y",
        "china_finance": query.get("chrFinInd") == "Y",
        "high_operation_value_customer": query.get("hiOprValCustInd") == "Y",
    }
    cross_border_flags = {
        "small_export": query.get("crossBorderSmallExportSalesMark") == "Y",
        "service_trade": query.get("crossBorderServiceTradeMark") == "Y",
        "small_service_trade": query.get("crossBorderSmallServiceTradeMark") == "Y",
        "labels": as_list(query.get("crossBorderLabel")),
    }
    shareholder_summary = _merge_shareholders(shareholders)[:8]
    required = [
        company.get("company_name"),
        company.get("credit_code"),
        first_nonempty(company.get("industry"), company.get("industry_big")),
        company.get("employee_count"),
        company.get("business_scope"),
    ]
    recommended = [
        label_names,
        ip_counts(ip),
        risk_counts(risk),
        recruitments,
        bidding.get("total_count"),
        qualifications,
        shareholder_summary,
    ]
    profile_completeness = (sum(bool(v) for v in required) / len(required) * 0.65) + (
        sum(bool(v) for v in recommended) / len(recommended) * 0.35
    )
    return {
        "credit_code": company["credit_code"],
        "company_name": company["company_name"],
        "industry": company.get("industry"),
        "industry_big": company.get("industry_big"),
        "industry_mid": company.get("industry_mid"),
        "industry_small": company.get("industry_small"),
        "employee_count": company.get("employee_count"),
        "employee_count_source": company.get("employee_count_source"),
        "registered_capital": company.get("registered_capital"),
        "registered_location": company.get("registered_location"),
        "province": company.get("province"),
        "county": company.get("county"),
        "business_scope": company.get("business_scope"),
        "established_at": company.get("established_at"),
        "legal_person": company.get("legal_person"),
        "reg_status": company.get("reg_status"),
        "company_org_type": company.get("company_org_type"),
        "is_listed": bool(company.get("stock_code") or company.get("listed_company_state")),
        "stock_code": company.get("stock_code"),
        "stock_short_name": company.get("stock_short_name"),
        "website": company.get("website"),
        "labels": json_text(label_names),
        "raw_label_codes": json_text(raw_label_codes),
        "bank_flags": json_text(bank_flags),
        "cross_border_flags": json_text(cross_border_flags),
        "ip_counts": json_text(ip_counts(ip)),
        "risk_counts": json_text(risk_counts(risk)),
        "recruitment_count": len(recruitments),
        "recent_recruitment_titles": json_text([row["title"] for row in recruitments[:5]]),
        "bidding_total": bidding.get("total_count"),
        "branch_count": len(branches),
        "shareholder_summary": json_text(shareholder_summary),
        "qualification_count": len(qualifications),
        "financing_event_count": len(financing_events),
        "outbound_investment_count": len(outbound_investments),
        "source_files": json_text(sorted(raw_files)),
        "missing_v1_files": json_text(missing_v1_files),
        "profile_completeness": round(profile_completeness, 4),
        "import_status": import_status_value,
        "updated_at": updated_at,
    }


def ip_counts(ip: JsonDict) -> JsonDict:
    return {
        "trademark": ip.get("trademark_count") or 0,
        "patent": ip.get("patent_count") or 0,
        "software": ip.get("software_count") or 0,
        "works_copyright": ip.get("works_copyright_count") or 0,
        "website_filing": ip.get("website_filing_count") or 0,
        "product": ip.get("product_count") or 0,
    }


def risk_counts(risk: JsonDict) -> JsonDict:
    return {
        "self": risk.get("self_risk_count") or 0,
        "pre": risk.get("pre_risk_count") or 0,
        "around": risk.get("around_risk_count") or 0,
        "court_session": risk.get("court_session_count") or 0,
        "judgement_doc": risk.get("judgement_doc_count") or 0,
        "announcement": risk.get("announcement_count") or 0,
        "inspection": risk.get("inspection_count") or 0,
        "change": risk.get("change_count") or 0,
    }
