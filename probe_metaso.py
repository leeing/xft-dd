"""Metaso 探针脚本 — 逐维度实测秘塔查询质量

用法：
    uv run probe_metaso.py "佛山市固特家居制品有限公司"
    uv run probe_metaso.py "某公司" --config config.yaml

输出：每条 query 的原始回答（带长度）+ 最终评级
  ✅ 有料  (≥100 字)
  ⚠️  太短  (20-99 字)
  ❌ 无答案 (<20 字 或 空)
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

_out = sys.stdout.write

_GOOD_CHARS = 100
_SHORT_CHARS = 20
_PREVIEW_CHARS = 300
_QUERY_DISPLAY_LEN = 55


def _ln(s: str = "") -> None:
    _out(s + "\n")


def _classify(chars: int) -> str:
    if chars >= _GOOD_CHARS:
        return "✅ 有料"
    if chars >= _SHORT_CHARS:
        return "⚠️  太短"
    return "❌ 无答案"


def _preview(answer: str) -> str:
    text = answer[:_PREVIEW_CHARS].replace("\n", " ").strip()
    return text + "…" if len(answer) > _PREVIEW_CHARS else text


async def _query_one(query_metaso, api_key: str, query: str, dim_name: str) -> dict:
    _ln(f"│  ▶ 查询：{query}")
    try:
        answer = await asyncio.wait_for(
            query_metaso(api_key, query, timeout=30),
            timeout=35,
        )
    except TimeoutError:
        _ln("│    ❌ 超时")
        return {"dim": dim_name, "q": query, "status": "timeout", "chars": 0, "answer": ""}
    except (OSError, ValueError) as exc:
        _ln(f"│    ❌ 错误：{exc}")
        return {"dim": dim_name, "q": query, "status": "error", "chars": 0, "answer": ""}

    chars = len(answer)
    status = _classify(chars)
    _ln(f"│    {status} ({chars} 字)")
    _ln(f"│    {_preview(answer)}")
    return {"dim": dim_name, "q": query, "status": status, "chars": chars, "answer": answer}


def _print_summary(results: list[dict], dims: list) -> None:
    _ln()
    _ln("=" * 70)
    _ln("  汇总")
    _ln("=" * 70)
    total = len(results)
    good = sum(1 for r in results if "✅" in r["status"])
    short = sum(1 for r in results if "⚠️" in r["status"])
    empty = total - good - short
    _ln(f"  ✅ 有料：{good}/{total}  ⚠️ 太短：{short}/{total}  ❌ 无答案：{empty}/{total}")
    _ln()

    for r in results:
        icon = r["status"].split()[0] if r["status"] not in ("timeout", "error") else "❌"
        q_short = r["q"][:_QUERY_DISPLAY_LEN] + ("…" if len(r["q"]) > _QUERY_DISPLAY_LEN else "")
        _ln(f"  {icon}  [{r['dim']}]  {q_short}  ({r['chars']}字)")
    _ln()

    empty_dims: list[str] = []
    weak_dims: list[str] = []
    for dim in dims:
        dim_results = [r for r in results if r["dim"] == dim.name]
        good_count = sum(1 for r in dim_results if "✅" in r["status"])
        if good_count == 0:
            short_count = sum(1 for r in dim_results if "⚠️" in r["status"])
            target_list = weak_dims if short_count > 0 else empty_dims
            target_list.append(dim.name)

    if empty_dims:
        _ln("  ⚠️  以下维度 Metaso 完全无答案，建议移除 metaso_queries：")
        for d in empty_dims:
            _ln(f"      - {d}")
    if weak_dims:
        _ln("  💡 以下维度 Metaso 答案偏短，建议优化查询措辞：")
        for d in weak_dims:
            _ln(f"      - {d}")
    if not empty_dims and not weak_dims:
        _ln("  🎉 所有维度均有有效答案！")
    _ln()


async def probe(target: str, config_path: str) -> None:
    from diligence.config import load_config
    from diligence.settings import settings
    from diligence.utils.metaso import query_metaso

    if not settings.metaso_api_key:
        sys.stderr.write("❌ METASO_API_KEY 未设置，请检查 .env\n")
        sys.exit(1)

    cfg = load_config(config_path)
    dims = [d for d in cfg.dimensions if d.enabled and d.metaso_queries]
    total_queries = sum(len(d.metaso_queries) for d in dims)

    _ln()
    _ln("=" * 70)
    _ln(f"  目标企业：{target}")
    _ln(f"  待探测维度：{len(dims)} 个，共 {total_queries} 条查询")
    _ln("=" * 70)
    _ln()

    results: list[dict] = []

    for dim in dims:
        _ln(f"┌── [{dim.name}] ({len(dim.metaso_queries)} 条查询) {'─' * 40}")
        for q in dim.metaso_queries:
            query = q.replace("{target}", target)
            r = await _query_one(query_metaso, settings.metaso_api_key, query, dim.name)
            results.append(r)
        _ln(f"└{'─' * 60}")
        _ln()

    _print_summary(results, dims)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Metaso 探针：测试各维度查询质量")
    parser.add_argument("target", help="目标企业名称")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    asyncio.run(probe(args.target, args.config))
