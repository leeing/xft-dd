from __future__ import annotations

import json
from pathlib import Path

import duckdb

from xft.warehouse.prophet_loader import discover_company_packages, load_prophet_data


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_discover_company_packages_reads_meta_name(tmp_path: Path) -> None:
    company_dir = tmp_path / "91440606707539050R_目录名"
    _write_json(
        company_dir / ".meta.json",
        {
            "company_name": "广东德美精细化工集团股份有限公司",
            "credit_code": "91440606707539050R",
            "fetchers": {},
        },
    )
    (tmp_path / ".cache").mkdir()

    packages = discover_company_packages(tmp_path)

    assert len(packages) == 1
    assert packages[0].company_name == "广东德美精细化工集团股份有限公司"
    assert packages[0].credit_code == "91440606707539050R"


def test_load_prophet_data_builds_profile_and_statuses(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    rich = input_root / "91440606707539050R_广东德美精细化工集团股份有限公司"
    meta_only = input_root / "91441200632828391P_肇庆市宝信金属实业有限公司"
    _write_json(
        rich / ".meta.json",
        {
            "company_name": "广东德美精细化工集团股份有限公司",
            "credit_code": "91440606707539050R",
            "fetchers": {"info": {"fetched_at": "2026-05-15T16:18:14+00:00"}},
        },
    )
    _write_json(
        rich / "info.json",
        {
            "code": 200,
            "data": {
                "info": {
                    "info": {
                        "name": "广东德美精细化工集团股份有限公司",
                        "unifiedSocialCreditCode": "91440606707539050R",
                        "cate1": "制造业",
                        "cate2": "化学原料和化学制品制造业",
                        "cate3": "纺织化学品",
                        "businessScope": "精细化学品生产销售",
                        "regCapital": "48211.5452万元人民币",
                        "legalPersonName": "黄冠雄",
                        "listedCompanyState": 1,
                    }
                }
            },
        },
    )
    _write_json(
        rich / "query_company.json",
        {
            "code": 200,
            "data": {
                "entName": "广东德美精细化工集团股份有限公司",
                "employeeNum": 1779,
                "idtCtgNm": "制造业",
                "industryBig": "化学原料和化学制品制造业",
                "highQualityCustomer": "Y",
                "crossBorderSmallExportSalesMark": "N",
            },
        },
    )
    _write_json(
        rich / "label.json",
        {
            "company_name": "广东德美精细化工集团股份有限公司",
            "labels": ["高质量客户"],
            "raw_label_codes": ["high_value.png"],
        },
    )
    _write_json(rich / "intellectual.json", {"data": {"intellectual": [{"name": "专利查询", "messageNo": 3}]}})
    _write_json(rich / "risk_insight.json", {"data": {"riskCount": {"selfRisk": 2, "preRisk": 1, "arroundRisk": 0}}})
    _write_json(
        rich / "recruit_message.json",
        {"data": {"list": [{"title": "生产主管", "city": "佛山", "source": "智联招聘"}]}},
    )
    _write_json(rich / "query_bidding_total.json", {"data": {"total": 5}})
    _write_json(rich / "query_qualification.json", {"data": [{"labNm": "高新技术企业认定", "ancNm": "科技资质"}]})
    _write_json(rich / "staff.json", {"data": {"list": [{"name": "黄冠雄", "staffTypeName": "董事长"}]}})
    _write_json(rich / "shareholder.json", {"data": {"list": [{"investorName": "股东A", "subscribeAmount": "100"}]}})
    _write_json(
        meta_only / ".meta.json",
        {"company_name": "肇庆市宝信金属实业有限公司", "credit_code": "91441200632828391P", "fetchers": {}},
    )

    output_db = tmp_path / "warehouse.duckdb"
    summary = load_prophet_data(input_root=input_root, output_db=output_db)

    assert summary.companies == 2
    assert summary.raw_json_rows == 12
    assert summary.import_status_counts == {"complete_or_rich": 1, "meta_only": 1}

    conn = duckdb.connect(str(output_db))
    try:
        profile = conn.execute(
            "select industry, employee_count, recruitment_count, bidding_total, import_status "
            "from company_profile where credit_code = ?",
            ["91440606707539050R"],
        ).fetchone()
        assert profile == ("制造业", 1779, 1, 5, "complete_or_rich")
        assert conn.execute("select count(*) from raw_company_json").fetchone()[0] == 12
    finally:
        conn.close()
