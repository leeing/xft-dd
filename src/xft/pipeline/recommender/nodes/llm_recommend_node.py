"""Create final recommendations from match results."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.ai.llm_trace import (
    exception_summary,
    llm_event,
    preview_text,
    print_llm_failure,
    print_llm_start,
    print_llm_success,
)
from xft.evidence.models import EvidenceRecord
from xft.pipeline.recommender.config_loader import load_prompt
from xft.pipeline.recommender.models import (
    ConflictSummaryItem,
    DimensionAnalysis,
    DimensionEvidenceSummary,
    EvidenceSummary,
    EvidenceTraceItem,
    ProductModule,
    RecommendationItem,
    RecommendationOutput,
    ScoreBreakdown,
)
from xft.pipeline.recommender.recommendation_normalizer import normalize_recommendation_payload
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.scoring import score_products
from xft.scoring.models import ProductScoreResult, ScoringContext, ScoringRunResult
from xft.settings import settings

LLM_TIMEOUT_SECONDS = 60
RECOMMEND_SCORE_THRESHOLD = 55


class _RecommendationPayload(BaseModel):
    summary: str
    recommendations: list[RecommendationItem]


def _priority_by_id(products: list[ProductModule]) -> dict[str, int]:
    return {item.module_id: item.priority for item in products}


def _clamp_score(value: int) -> int:
    return max(0, min(value, 100))


def _related_analyses(state: RecommenderState, product: ProductModule) -> list[DimensionAnalysis]:
    return [item for item in state["dimension_analysis"] if item.dimension_id in product.target_needs]


def _score_breakdown(product: ProductModule, analyses: list[DimensionAnalysis], final_score: int) -> ScoreBreakdown:
    supported = sum(1 for item in analyses if item.status == "supported")
    partial = sum(1 for item in analyses if item.status == "partial")
    local_count = sum(len(item.local_evidence) for item in analyses)
    confirmation_count = sum(
        len([ev for ev in item.web_evidence if ev.relation_to_profile == "confirmation"]) for item in analyses
    )
    supplement_count = sum(
        len([ev for ev in item.web_evidence if ev.relation_to_profile not in ("confirmation", "conflict")])
        for item in analyses
    )
    conflict_count = sum(len(item.conflicts) for item in analyses)
    missing_count = sum(len(item.missing_evidence) for item in analyses)
    return ScoreBreakdown(
        base_priority=int(product.priority * 0.45),
        dimension_support=supported * 22 + partial * 10,
        evidence_support=min(20, local_count * 4),
        web_support=min(12, confirmation_count * 3) + min(8, supplement_count),
        missing_evidence_penalty=-min(15, missing_count),
        conflict_penalty=-(conflict_count * 8),
        final_score=_clamp_score(final_score),
    )


def _trace_from_evidence(ev: EvidenceRecord) -> EvidenceTraceItem:
    return EvidenceTraceItem(
        evidence_id=ev.evidence_id,
        dimension_id=ev.dimension_id,
        source_type=ev.source_type,
        source_name=ev.source_name,
        source_url=ev.source_url,
        source_field=ev.source_field,
        claim=ev.claim,
        confidence=ev.confidence,
        relation_to_profile=ev.relation_to_profile,
    )


def _evidence_trace(analyses: list[DimensionAnalysis], limit: int = 8) -> list[EvidenceTraceItem]:
    ordered: list[EvidenceRecord] = []
    for analysis in analyses:
        ordered.extend(analysis.local_evidence)
        ordered.extend([ev for ev in analysis.web_evidence if ev.relation_to_profile == "confirmation"])
        ordered.extend(
            [ev for ev in analysis.web_evidence if ev.relation_to_profile not in ("confirmation", "conflict")]
        )
        ordered.extend(analysis.inference_evidence)
    return [_trace_from_evidence(ev) for ev in ordered[:limit]]


def _trace_from_scored_evidence(score: ProductScoreResult, limit: int = 8) -> list[EvidenceTraceItem]:
    return [_trace_from_evidence(ev) for ev in score.evidence[:limit]]


def _evidence_summary(state: RecommenderState) -> EvidenceSummary:
    summaries = [
        DimensionEvidenceSummary(
            dimension_id=analysis.dimension_id,
            title=analysis.title,
            local_evidence_count=len(analysis.local_evidence),
            web_evidence_count=len(analysis.web_evidence),
            inference_evidence_count=len(analysis.inference_evidence),
            conflict_count=len(analysis.conflicts),
            missing_evidence_count=len(analysis.missing_evidence),
            status=analysis.status,
            confidence=analysis.confidence,
        )
        for analysis in state["dimension_analysis"]
    ]
    return EvidenceSummary(
        local_evidence_count=sum(item.local_evidence_count for item in summaries),
        web_evidence_count=sum(item.web_evidence_count for item in summaries),
        inference_evidence_count=sum(item.inference_evidence_count for item in summaries),
        conflict_count=sum(item.conflict_count for item in summaries),
        missing_evidence_count=sum(item.missing_evidence_count for item in summaries),
        by_dimension=summaries,
    )


def _conflict_summary(state: RecommenderState) -> list[ConflictSummaryItem]:
    return [
        ConflictSummaryItem(
            dimension_id=conflict.dimension_id,
            claim=conflict.claim,
            conflict_note=conflict.conflict_note,
            resolution=conflict.resolution,
            source_url=conflict.source_url,
        )
        for analysis in state["dimension_analysis"]
        for conflict in analysis.conflicts
    ]


def _scoring_run(state: RecommenderState) -> ScoringRunResult:
    return score_products(
        products=state["products"],
        context=ScoringContext(
            company_profile=state.get("profile", {}),
            dimension_analyses=state["dimension_analysis"],
        ),
        policy=state["scoring_policy"],
    )


def _with_explainability(
    output: RecommendationOutput,
    state: RecommenderState,
    scoring: ScoringRunResult | None = None,
) -> RecommendationOutput:
    scoring = scoring or _scoring_run(state)
    product_by_id = {item.module_id: item for item in state["products"]}
    score_by_id = {item.product.module_id: item for item in scoring.product_scores}
    score_order = {item.product.module_id: idx for idx, item in enumerate(scoring.product_scores)}
    explained: list[RecommendationItem] = []
    for item in output.recommendations:
        product = product_by_id.get(item.module_id)
        if product is None:
            explained.append(item)
            continue
        analyses = _related_analyses(state, product)
        scored = score_by_id.get(item.module_id)
        score_breakdown = scored.score_breakdown if scored else _score_breakdown(product, analyses, item.score)
        final_score = scored.final_score if scored else item.score
        evidence_trace = _trace_from_scored_evidence(scored) if scored else _evidence_trace(analyses)
        explained.append(
            item.model_copy(
                update={
                    "rank": 0,
                    "score": final_score,
                    "score_breakdown": score_breakdown,
                    "evidence_trace": evidence_trace or item.evidence_trace or _evidence_trace(analyses),
                    "data_gaps": scored.data_gaps[:8] if scored else item.data_gaps,
                }
            )
        )
    explained = sorted(explained, key=lambda rec: (-rec.score, score_order.get(rec.module_id, len(score_order))))
    explained = [item.model_copy(update={"rank": idx}) for idx, item in enumerate(explained, 1)]
    return output.model_copy(
        update={
            "recommendations": explained,
            "evidence_summary": output.evidence_summary
            if output.evidence_summary.by_dimension
            else _evidence_summary(state),
            "conflict_summary": output.conflict_summary or _conflict_summary(state),
            "scoring_summary": scoring.summary,
        }
    )


def _build_evidence_summary(state: RecommenderState) -> str:
    """Build a concise evidence summary for fallback recommendations."""
    analyses = state["dimension_analysis"]
    parts: list[str] = []
    total_conflicts = sum(len(a.conflicts) for a in analyses)
    total_primary = sum(len(a.local_evidence) for a in analyses)
    total_web = sum(len(a.web_evidence) for a in analyses)

    if total_primary:
        parts.append(f"本地证据 {total_primary} 条")
    if total_web:
        parts.append(f"Web 补证 {total_web} 条")
    if total_conflicts:
        parts.append(f"数据冲突 {total_conflicts} 处（已采用本地证据）")

    if not parts:
        return "本地画像证据有限，建议先核实关键数据后再形成正式方案。"
    return "；".join(parts) + "。"


def _fallback_recommendation(state: RecommenderState) -> RecommendationOutput:
    priorities = _priority_by_id(state["products"])
    scoring = _scoring_run(state)
    match_by_id = {item.module_id: item for item in state["match_results"]}
    ranked_scores = [
        item for item in scoring.product_scores if item.final_score >= RECOMMEND_SCORE_THRESHOLD and not item.excluded
    ]
    if not ranked_scores:
        ranked_scores = scoring.product_scores[:3]
    recs: list[RecommendationItem] = []
    evidence_summary = _build_evidence_summary(state)

    for idx, scored in enumerate(ranked_scores[:5], 1):
        product = scored.product
        item = match_by_id.get(product.module_id)
        # Build pitch based on evidence composition
        related = [a for a in state["dimension_analysis"] if a.dimension_id in product.target_needs]
        has_conflict = any(a.conflicts for a in related)
        has_web = any(a.web_evidence for a in related)
        business_need = item.business_need if item else product.match_rule

        if has_conflict:
            pitch = f"可围绕「{business_need}」切入，注意 Web 信息与本地画像存在冲突，建议先核实后再推进。"
        elif has_web:
            pitch = f"可围绕「{business_need}」切入，本地画像与 Web 补证均支持该方向。"
        else:
            pitch = f"可围绕「{business_need}」切入，基于本地企业画像线索进行需求确认。"

        matched_rule_reasons = [rule.reason for rule in scored.score_breakdown.matched_rules[:2]]
        reason = "；".join(matched_rule_reasons) or (
            item.reason if item else "基于产品规则、企业画像和证据质量评分生成。"
        )

        recs.append(
            RecommendationItem(
                rank=idx,
                module_id=product.module_id,
                module_name=product.module_name,
                score=scored.final_score,
                priority=priorities.get(product.module_id, 0),
                business_need=business_need,
                reason=reason,
                suggested_pitch=pitch,
                evidence_dimensions=[analysis.title for analysis in related if analysis.status != "insufficient"],
                data_gaps=scored.data_gaps[:8],
                score_breakdown=scored.score_breakdown,
                evidence_trace=_trace_from_scored_evidence(scored),
            )
        )

    profile = state.get("profile", {})
    summary = (
        f"{profile.get('company_name', state['company_name'])} 当前基于本地 DuckDB 画像生成推荐；{evidence_summary}"
    )
    output = RecommendationOutput(
        company_name=str(profile.get("company_name") or state["company_name"]),
        scenario=state.get("scenario_id") or state["products_config"].scenario,
        scenario_name=state.get("scenario_name"),
        summary=summary,
        recommendations=recs,
        needs_web_enrichment=state["needs_web_enrichment"],
        profile_completeness=float(profile.get("profile_completeness") or 0),
        evidence_summary=_evidence_summary(state),
        conflict_summary=_conflict_summary(state),
        scoring_summary=scoring.summary,
    )
    normalized = normalize_recommendation_payload(
        output,
        company_name=output.company_name,
        scenario=output.scenario,
        products=state["products"],
        match_results=state["match_results"],
        needs_web_enrichment=output.needs_web_enrichment,
        profile_completeness=output.profile_completeness,
    )
    normalized = normalized.model_copy(update={"scenario_name": output.scenario_name})
    return _with_explainability(normalized, state, scoring)


async def llm_recommend_node(state: RecommenderState) -> dict[str, object]:
    events: list[dict[str, Any]] = []
    if not state.get("use_llm", True) or not (settings.llm_api_key or settings.minimax_api_key):
        recommendation = _fallback_recommendation(state)
        display.info(f"规则兜底推荐 → {len(recommendation.recommendations)} 个推荐")
        return {"recommendation": recommendation, "llm_call_events": events}
    try:
        system_prompt = load_prompt(
            Path(
                state.get("prompt_paths", {}).get(
                    "recommend_system",
                    "config/recommender/prompts/recommend_system.md",
                )
            )
        )
        priorities = _priority_by_id(state["products"])
        payload = {
            "company_profile": state["profile"],
            "dimension_analysis": [item.model_dump() for item in state["dimension_analysis"]],
            "match_results": [item.model_dump() for item in state["match_results"]],
            "product_priorities": priorities,
        }
        request_summary = {
            "matches": len(state["match_results"]),
            "dimensions": len(state["dimension_analysis"]),
            "timeout_seconds": LLM_TIMEOUT_SECONDS,
        }
        if state.get("llm_debug", False):
            print_llm_start(title="推荐生成", model=settings.llm_model, request=request_summary)
        started = perf_counter()
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
        events.append(
            llm_event(
                stage="recommendation",
                name="llm_recommend",
                model=settings.llm_model,
                status="success",
                elapsed_seconds=perf_counter() - started,
                request=request_summary,
                response_preview=preview_text(raw),
                response_text=raw,
                system_prompt=system_prompt,
                user_payload=payload,
                parameters={"temperature": 0.1, "timeout_seconds": LLM_TIMEOUT_SECONDS},
            )
        )
        if state.get("llm_debug", False):
            print_llm_success(title="推荐生成", elapsed_seconds=perf_counter() - started, raw=raw)
        parsed: Any = json.loads(extract_json(raw))
        fallback = _fallback_recommendation(state)
        scoring = _scoring_run(state)
        try:
            rec_payload = _RecommendationPayload.model_validate(parsed)
            parsed_payload: Any = rec_payload
        except ValidationError:
            parsed_payload = parsed
        recommendation = normalize_recommendation_payload(
            parsed_payload,
            company_name=str(state["profile"].get("company_name") or state["company_name"]),
            scenario=state.get("scenario_id") or state["products_config"].scenario,
            products=state["products"],
            match_results=state["match_results"],
            needs_web_enrichment=state["needs_web_enrichment"],
            profile_completeness=float(state["profile"].get("profile_completeness") or 0),
            fallback=fallback,
        )
        recommendation = _with_explainability(recommendation, state, scoring)
        display.ok(f"LLM 推荐完成 → {len(recommendation.recommendations)} 个推荐产品")
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, KeyError, TypeError, ValueError) as exc:
        if "started" in locals():
            events.append(
                llm_event(
                    stage="recommendation",
                    name="llm_recommend",
                    model=settings.llm_model,
                    status="failed",
                    elapsed_seconds=perf_counter() - started,
                    request=locals().get("request_summary", {}),
                    system_prompt=locals().get("system_prompt", ""),
                    user_payload=locals().get("payload"),
                    parameters={"temperature": 0.1, "timeout_seconds": LLM_TIMEOUT_SECONDS},
                    error=exc,
                )
            )
        recommendation = _fallback_recommendation(state)
        if state.get("llm_debug", False) and "started" in locals():
            print_llm_failure(
                title="推荐生成",
                elapsed_seconds=perf_counter() - started,
                error=exc,
                fallback=f"规则兜底推荐，推荐 {len(recommendation.recommendations)} 个",
            )
        display.info(f"LLM 失败 ({exception_summary(exc)}), 规则兜底 → {len(recommendation.recommendations)} 个推荐")
    for rec in recommendation.recommendations[:5]:
        display.branch(f"#{rec.rank} {rec.module_name}: {rec.score}分 — {rec.suggested_pitch[:60]}...")
    return {"recommendation": recommendation, "llm_call_events": events}
