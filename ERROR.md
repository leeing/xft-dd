# LLM 输出解析错误分析

## 错误类型

### 1. JSONDecodeError — JSON 格式不合法

**现象**：`Expecting ',' delimiter: line 4 column 31 (char 80)`

**根因**：LLM（MiniMax M2.7）输出 JSON 时偶尔产生语法错误（缺逗号、引号不配对、多余字符等）。当前仅通过 system prompt 文本要求输出 JSON，模型不受输出格式约束。

**触发位置**：`business_evaluator.py:_evaluate_llm_indicator` → `json.loads(extract_json(raw))`

**当前降级**：捕获异常后走 `_fallback_indicator_result()`，用 `evidence_hints` 在 profile 中模糊匹配。

**根治方案**：添加 `response_format={"type": "json_object"}` 到 API 调用

```python
# business_evaluator.py _evaluate_llm_indicator
resp = await client.chat.completions.create(
    model=settings.llm_model,
    messages=[...],
    temperature=0.0,
    timeout=LLM_TIMEOUT_SECONDS,
    response_format={"type": "json_object"},  # ← 强制 LLM 输出合法 JSON
)
```

`json_object` 模式保证输出一定是合法 JSON（MiniMax OpenAI 兼容 API 支持），从根本消除 JSONDecodeError。

### 2. ValidationError — JSON 合法但字段类型错误

**现象**：`evidence: Input should be a valid list [type=list_type, input_value="xxx", input_type=str]`

**根因**：模型把 `evidence` 字段输出为字符串（如 `"暂无证据"`）而非数组 `["暂无证据"]`。Pydantic `list[str]` 校验拒绝字符串类型。

**触发位置**：`_LlmIndicatorPayload.model_validate(parsed)`

**当前降级**：同 JSONDecodeError，走 fallback。

**根治方案**：在 `_LlmIndicatorPayload` 添加 `@field_validator` 容错

```python
class _LlmIndicatorPayload(BaseModel):
    result: BusinessResult
    confidence: BusinessConfidence
    current_status: str
    evidence: list[str] = []

    @field_validator("evidence", mode="before")
    @classmethod
    def coerce_evidence(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v] if v else []
        if isinstance(v, list):
            return v
        return []
```

这样 `evidence: "字符串"` → 自动包装为 `["字符串"]`，不再抛 ValidationError。

## 修复位置

| 文件 | 修改 |
|------|------|
| `src/xft/pipeline/recommender/business_evaluator.py` | 添加 `response_format={"type": "json_object"}` |
| `src/xft/pipeline/recommender/business_evaluator.py` | `_LlmIndicatorPayload` 添加 `@field_validator("evidence")` |

## 注意事项

- `response_format` 需要 MiniMax API 支持 `json_object` 模式（OpenAI 兼容接口普遍支持）
- `@field_validator` 容错不应掩盖模型持续输出错误类型的问题——如果某个指标频繁触发容错，说明该指标的 prompt/standard 需要调整
- 两处修改合计约 10 行代码，无破坏性变更
