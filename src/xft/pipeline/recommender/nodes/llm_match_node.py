"""Match product modules against dimension analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.pipeline.recommender.config_loader import load_prompt
from xft.pipeline.recommender.models import DimensionAnalysis, MatchResult, ProductModule
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.settings import settings

MATCH_SCORE_THRESHOLD = 55
HIGH_CONFIDENCE_SCORE_THRESHOLD = 75
MEDIUM_CONFIDENCE_SCORE_THRESHOLD = 60
LLM_TIMEOUT_SECONDS = 60


class _MatchList(BaseModel):
    matches: list[MatchResult]


def _facts_for_dimensions(analyses: list[DimensionAnalysis], target_needs: list[str]) -> list[str]:
    facts: list[str] = []
    for item in analyses:
        if item.dimension_id not in target_needs:
            continue
        facts.extend(e.claim for e in item.local_evidence[:4])
        if not item.local_evidence:
            facts.extend(fact.claim for fact in item.facts[:4])
        facts.extend(e.claim for e in item.inference_evidence[:2])
        if not item.inference_evidence:
            facts.extend(item.inferences[:2])
        facts.extend(e.claim for e in item.web_evidence[:3] if e.relation_to_profile != "conflict")
        facts.extend(f"数据冲突：{e.conflict_note or e.claim}" for e in item.conflicts[:2])
    return facts


def _evidence_score_for_product(
    product: ProductModule,
    analyses: list[DimensionAnalysis],
) -> int:
    """Compute an evidence-driven score adjustment for fallback matching.

    Base formula (preserved from original):
        base = product.priority * 0.45 + supported*22 + partial*10

    Adjustment from resolved evidence:
        + primary evidence * 4
        + confirmation evidence * 3
        + supplement evidence * 1
        - conflict evidence * 8
    """
    related = [item for item in analyses if item.dimension_id in product.target_needs]
    if not related:
        return 0

    supported = sum(1 for item in related if item.status == "supported")
    partial = sum(1 for item in related if item.status == "partial")
    base_score = int(product.priority * 0.45 + supported * 22 + partial * 10)

    # Evidence quality adjustment
    primary_count = sum(len(item.local_evidence) for item in related)
    confirmation_count = sum(
        len([e for e in item.web_evidence if getattr(e, "relation_to_profile", "") == "confirmation"])
        for item in related
    )
    supplement_count = sum(
        len([e for e in item.web_evidence if getattr(e, "relation_to_profile", "") not in ("conflict", "confirmation")])
        for item in related
    )
    conflict_count = sum(len(item.conflicts) for item in related)

    adjustment = (
        min(20, primary_count * 4)
        + min(12, confirmation_count * 3)
        + min(8, supplement_count)
        - conflict_count * 8
    )

    return min(100, max(0, base_score + adjustment))


def _fallback_match(products: list[ProductModule], analyses: list[DimensionAnalysis]) -> list[MatchResult]:
    by_id = {item.dimension_id: item for item in analyses}
    results: list[MatchResult] = []
    for product in products:
        related = [by_id[dim_id] for dim_id in product.target_needs if dim_id in by_id]
        score = _evidence_score_for_product(product, analyses)
        evidence = _facts_for_dimensions(analyses, product.target_needs)
        missing = sorted({gap for item in related for gap in item.missing_evidence[:3]})

        # Confidence derives from evidence composition
        has_conflict = any(item.conflicts for item in related)
        has_primary = any(item.local_evidence for item in related)
        has_web = any(item.web_evidence for item in related)

        if has_primary and not has_conflict:
            confidence: Any = "高" if score >= HIGH_CONFIDENCE_SCORE_THRESHOLD else "中"
        elif has_web or has_primary:
            confidence = "中" if score >= MEDIUM_CONFIDENCE_SCORE_THRESHOLD else "低"
        else:
            confidence = "待补充"

        results.append(
            MatchResult(
                module_id=product.module_id,
                module_name=product.module_name,
                matched=score >= MATCH_SCORE_THRESHOLD,
                score=score,
                confidence=confidence,
                business_need=product.match_rule,
                reason="基于本地企业画像和配置维度的规则兜底匹配。"
                + (" 存在数据冲突，已采用本地证据。" if has_conflict else ""),
                supporting_dimensions=[item.title for item in related if item.status != "insufficient"],
                evidence_summary=evidence[:8],
                missing_evidence=missing[:8],
            )
        )
    return results


async def llm_match_node(state: RecommenderState) -> dict[str, object]:
    display.phase(4, 5, "产品匹配")
    products = state["products"]
    analyses = state["dimension_analysis"]
    prompt_path = Path(state.get("prompt_paths", {}).get("match_system", "config/recommender/prompts/match_system.md"))
    if not state.get("use_llm", True) or not (settings.llm_api_key or settings.minimax_api_key):
        matches = _fallback_match(products, analyses)
        display.info(f"规则兜底匹配 → {len(matches)} 个候选产品")
        return {"match_results": matches}
    try:
        system_prompt = load_prompt(prompt_path)
        payload = {
            "company_profile": state["profile"],
            "dimension_analysis": [item.model_dump() for item in analyses],
            "products": [item.model_dump() for item in products],
        }
        client = get_ai_client()
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            temperature=0.1,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed: Any = json.loads(extract_json(raw))
        matches = _MatchList.model_validate(parsed).matches
        display.ok(f"LLM 分析完成 → {len(matches)} 个候选产品")
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, KeyError, TypeError, ValueError):
        matches = _fallback_match(products, analyses)
        display.info(f"LLM 失败, 规则兜底 → {len(matches)} 个候选产品")
    for m in matches:
        icon = "✓" if m.matched else "✗"
        display.branch(f"{icon} {m.module_name}: 得分 {m.score} ({m.confidence})")
    return {"match_results": matches}
