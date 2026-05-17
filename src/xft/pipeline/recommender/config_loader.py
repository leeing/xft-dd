"""Configuration loading for the recommender."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from xft.cache.hashing import stable_json_hash
from xft.core.config_loader import load_dimensions_config, load_prompt, read_yaml
from xft.pipeline.recommender.models import ProductModule, ProductsConfig
from xft.pipeline.recommender.scenario import ScenarioBundle, maybe_scenario_path

__all__ = ["load_products_config", "load_dimensions_config", "load_prompt", "write_products_resolved_config"]

_PRODUCT_SET_FIELDS = {
    "module_name",
    "priority",
    "target_needs",
    "match_rule",
    "base_score",
}


class ProductPatch(BaseModel):
    """Declarative patch for one product module inside a scenario bundle."""

    module_id: str
    set: dict[str, Any] = Field(default_factory=dict)
    append_positive_rules: list[dict[str, Any]] = Field(default_factory=list)
    append_negative_rules: list[dict[str, Any]] = Field(default_factory=list)
    append_exclusion_rules: list[dict[str, Any]] = Field(default_factory=list)
    replace_positive_rules: list[dict[str, Any]] = Field(default_factory=list)
    replace_negative_rules: list[dict[str, Any]] = Field(default_factory=list)
    replace_exclusion_rules: list[dict[str, Any]] = Field(default_factory=list)
    remove_positive_rules: list[str] = Field(default_factory=list)
    remove_negative_rules: list[str] = Field(default_factory=list)
    remove_exclusion_rules: list[str] = Field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    return read_yaml(path)


def load_products_config(path: str | Path) -> ProductsConfig:
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        config = load_products_config(scenario.products_path)
        return apply_product_patches(config, scenario.config.patches.get("products", []))
    config_path = Path(path)
    if config_path.is_dir():
        config_path = config_path / "products.yaml"
    return ProductsConfig.model_validate(_read_yaml(config_path))


def apply_product_patches(config: ProductsConfig, raw_patches: Any) -> ProductsConfig:
    """Apply scenario-level product patches by module_id."""
    if raw_patches in (None, ""):
        return config
    if not isinstance(raw_patches, list):
        msg = "scenario patches.products must be a list"
        raise TypeError(msg)
    patches = [ProductPatch.model_validate(item) for item in raw_patches]
    if not patches:
        return config
    products_by_id = {product.module_id: product for product in config.products}
    for patch in patches:
        product = products_by_id.get(patch.module_id)
        if product is None:
            msg = f"product patch references unknown module_id: {patch.module_id}"
            raise ValueError(msg)
        products_by_id[patch.module_id] = _apply_product_patch(product, patch)
    return config.model_copy(update={"products": [products_by_id[item.module_id] for item in config.products]})


def _apply_product_patch(product: ProductModule, patch: ProductPatch) -> ProductModule:
    payload = product.model_dump(mode="json")
    unknown_set_fields = sorted(set(patch.set) - _PRODUCT_SET_FIELDS)
    if unknown_set_fields:
        msg = f"unsupported product patch set field(s) for {patch.module_id}: {', '.join(unknown_set_fields)}"
        raise ValueError(msg)
    payload.update(patch.set)
    payload["positive_rules"] = _patch_rules(
        payload["positive_rules"],
        append=patch.append_positive_rules,
        replace=patch.replace_positive_rules,
        remove=patch.remove_positive_rules,
        label=f"{patch.module_id}.positive_rules",
    )
    payload["negative_rules"] = _patch_rules(
        payload["negative_rules"],
        append=patch.append_negative_rules,
        replace=patch.replace_negative_rules,
        remove=patch.remove_negative_rules,
        label=f"{patch.module_id}.negative_rules",
    )
    payload["exclusion_rules"] = _patch_rules(
        payload["exclusion_rules"],
        append=patch.append_exclusion_rules,
        replace=patch.replace_exclusion_rules,
        remove=patch.remove_exclusion_rules,
        label=f"{patch.module_id}.exclusion_rules",
    )
    return ProductModule.model_validate(payload)


def _patch_rules(
    rules: list[dict[str, Any]],
    *,
    append: list[dict[str, Any]],
    replace: list[dict[str, Any]],
    remove: list[str],
    label: str,
) -> list[dict[str, Any]]:
    by_id = {str(rule.get("id") or ""): dict(rule) for rule in rules}
    _validate_rule_ids(by_id, label=label)
    for rule_id in remove:
        if rule_id not in by_id:
            msg = f"cannot remove unknown rule {rule_id!r} from {label}"
            raise ValueError(msg)
        del by_id[rule_id]
    for rule in replace:
        rule_id = _rule_id(rule, label=label)
        if rule_id not in by_id:
            msg = f"cannot replace unknown rule {rule_id!r} in {label}"
            raise ValueError(msg)
        by_id[rule_id] = dict(rule)
    for rule in append:
        rule_id = _rule_id(rule, label=label)
        if rule_id in by_id:
            msg = f"cannot append duplicate rule {rule_id!r} to {label}"
            raise ValueError(msg)
        by_id[rule_id] = dict(rule)
    return list(by_id.values())


def _validate_rule_ids(rules: dict[str, dict[str, Any]], *, label: str) -> None:
    if "" in rules:
        msg = f"all rules in {label} must have an id"
        raise ValueError(msg)


def _rule_id(rule: dict[str, Any], *, label: str) -> str:
    rule_id = str(rule.get("id") or "")
    if not rule_id:
        msg = f"patched rule in {label} must have an id"
        raise ValueError(msg)
    return rule_id


def write_products_resolved_config(
    scenario: ScenarioBundle,
    config: ProductsConfig,
    path: str | Path | None = None,
) -> Path:
    """Write scenario_resolved.json with the effective patched products hash."""
    payload = scenario.resolved_payload()
    payload["products_effective_hash"] = stable_json_hash(config.model_dump(mode="json"))
    payload["products_effective_count"] = len(config.products)
    return scenario.write_resolved_config_payload(payload, path=path)
