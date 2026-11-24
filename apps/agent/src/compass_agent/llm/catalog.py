"""Offline LiteLLM catalog plus the System One decision presets.

The catalog is derived from the locked SDK's own model metadata; no network access
or database is involved. Removing an entry from a future SDK never invalidates an
already saved binding because execution uses the persisted adapter and SDK route.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from compass_agent.schemas import CatalogItem, CatalogPage, ModelCapabilities

from .capabilities import decision_capabilities, litellm_capabilities
from .contracts import AdapterKind, ModelKind

try:  # pragma: no cover - import shape depends on the locked SDK build
    from litellm._version import version as _SDK_VERSION

    SDK_VERSION = str(_SDK_VERSION)
except Exception:  # pragma: no cover
    SDK_VERSION = "unknown"


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    id: str
    vendor: str
    api_model: str
    kind: ModelKind
    adapter: AdapterKind
    source: Literal["litellm", "project_verified", "systemone"]
    base_url: str | None = None
    tools: bool | None = None
    thinking: bool | None = None
    schema_output: bool | None = None
    json_output: bool | None = None
    reasoning_efforts: tuple[str, ...] = ()

    @property
    def chat_supported(self) -> bool:
        return self.kind == "llm" and self.adapter == "litellm_chat"

    @property
    def capabilities(self) -> ModelCapabilities:
        if self.kind == "decision":
            return decision_capabilities()
        return litellm_capabilities(
            tools=self.tools,
            thinking=self.thinking,
            schema_output=self.schema_output,
            json_output=self.json_output,
            reasoning_efforts=list(self.reasoning_efforts),
        )

    def item(self) -> CatalogItem:
        return CatalogItem(
            id=self.id,
            vendor=self.vendor,
            api_model=self.api_model,
            kind=self.kind,
            adapter_supported=True,
            chat_supported=self.chat_supported,
            source=self.source,
            base_url=self.base_url,
            capabilities=self.capabilities,
        )


DECISION_VENDORS = frozenset({"aliyun", "typesafe", "systemone"})

_DECISION_PRESETS = (
    CatalogRecord(
        id="aliyun/decision-model-preview",
        vendor="aliyun",
        api_model="decision-model-preview",
        kind="decision",
        adapter="systemone_decision",
        source="systemone",
    ),
    CatalogRecord(
        id="typesafe/jev-latest",
        vendor="typesafe",
        api_model="jev-latest",
        kind="decision",
        adapter="systemone_decision",
        source="systemone",
        base_url="https://api.typesafe.ai/v1",
    ),
)

_PROJECT_VERIFIED = (
    CatalogRecord(
        id="deepseek/deepseek-v4-pro",
        vendor="deepseek",
        api_model="deepseek-v4-pro",
        kind="llm",
        adapter="litellm_chat",
        source="project_verified",
        tools=True,
        thinking=True,
        schema_output=True,
        json_output=True,
        reasoning_efforts=("low", "medium", "high", "max"),
    ),
    CatalogRecord(
        id="deepseek/deepseek-flash",
        vendor="deepseek",
        api_model="deepseek-flash",
        kind="llm",
        adapter="litellm_chat",
        source="project_verified",
        tools=True,
        thinking=True,
        schema_output=True,
        json_output=True,
        reasoning_efforts=("low", "medium", "high", "max"),
    ),
)


def _flag(info: dict, key: str) -> bool | None:
    value = info.get(key)
    return value if isinstance(value, bool) else None


def _reasoning_efforts(info: dict, vendor: str) -> tuple[str, ...]:
    if not info.get("supports_reasoning"):
        return ()
    if vendor == "deepseek":
        return ("low", "medium", "high", "max")
    return ("low", "medium", "high")


def _litellm_model_cost() -> dict:
    try:  # pragma: no cover - depends on the locked SDK build
        import litellm
    except Exception:  # pragma: no cover
        return {}
    return litellm.model_cost


def _litellm_records() -> list[CatalogRecord]:
    records: dict[str, CatalogRecord] = {}
    for key, info in _litellm_model_cost().items():
        if not isinstance(info, dict):
            continue
        if info.get("mode") not in {"chat", "responses"}:
            continue
        vendor = info.get("litellm_provider")
        if not isinstance(vendor, str) or not vendor:
            continue
        api_model = key[len(vendor) + 1 :] if key.startswith(f"{vendor}/") else key
        if not api_model:
            continue
        catalog_id = f"{vendor}/{api_model}"
        if catalog_id in records:
            continue
        records[catalog_id] = CatalogRecord(
            id=catalog_id,
            vendor=vendor,
            api_model=api_model,
            kind="llm",
            adapter="litellm_chat",
            source="litellm",
            tools=_flag(info, "supports_function_calling"),
            thinking=_flag(info, "supports_reasoning"),
            schema_output=_flag(info, "supports_response_schema"),
            json_output=_flag(info, "supports_json_mode")
            if _flag(info, "supports_json_mode") is not None
            else _flag(info, "supports_response_schema"),
            reasoning_efforts=_reasoning_efforts(info, vendor),
        )
    return list(records.values())


@lru_cache(maxsize=1)
def records() -> dict[str, CatalogRecord]:
    merged: dict[str, CatalogRecord] = {}
    for record in _DECISION_PRESETS:
        merged[record.id] = record
    for record in _litellm_records():
        merged[record.id] = record
    for record in _PROJECT_VERIFIED:
        merged.setdefault(record.id, record)
    return merged


@lru_cache(maxsize=1)
def _by_vendor_model() -> dict[tuple[str, str], CatalogRecord]:
    index: dict[tuple[str, str], CatalogRecord] = {}
    for record in records().values():
        index.setdefault((record.vendor, record.api_model), record)
    return index


def find_record(catalog_id: str, api_model: str | None = None, vendor: str | None = None):
    record = records().get(catalog_id)
    if record is not None:
        return record
    if api_model and vendor:
        return _by_vendor_model().get((vendor, api_model))
    return None


def list_records() -> list[CatalogRecord]:
    return sorted(records().values(), key=lambda record: record.id)


def page(
    *,
    q: str = "",
    vendor: str | None = None,
    page_number: int = 1,
    page_size: int = 20,
) -> CatalogPage:
    needle = q.strip().casefold()
    matches: list[tuple[int, CatalogRecord]] = []
    for record in list_records():
        if vendor and record.vendor != vendor:
            continue
        if (
            needle
            and needle not in record.id.casefold()
            and needle not in record.api_model.casefold()
        ):
            continue
        score = 1
        if needle and (
            record.vendor.casefold().startswith(needle) or record.id.casefold().startswith(needle)
        ):
            score = 0
        matches.append((score, record))
    matches.sort(key=lambda entry: (entry[0], entry[1].id))
    ranked = [record for _, record in matches]
    total = len(ranked)
    start = (page_number - 1) * page_size
    window = ranked[start : start + page_size]
    return CatalogPage(
        items=[record.item() for record in window],
        total=total,
        page=page_number,
        page_size=page_size,
        sdk_version=SDK_VERSION,
    )


def vendors() -> list[str]:
    return sorted({record.vendor for record in records().values()})


def classify_manual(catalog_id: str) -> tuple[ModelKind, AdapterKind, str, str]:
    """Resolve a manually typed provider/model identifier without a catalog entry."""

    if "/" not in catalog_id:
        raise ValueError("manual identifier must be provider/model")
    vendor, api_model = catalog_id.split("/", 1)
    vendor = vendor.strip().casefold()
    api_model = api_model.strip()
    if not vendor or not api_model:
        raise ValueError("manual identifier must be provider/model")
    if vendor in DECISION_VENDORS:
        return "decision", "systemone_decision", vendor, api_model
    return "llm", "litellm_chat", vendor, api_model


def capabilities_for(id_value: str, api_model: str, vendor: str) -> ModelCapabilities:
    record = find_record(id_value, api_model, vendor)
    if record is not None:
        return record.capabilities
    from .capabilities import unknown_llm_capabilities

    return unknown_llm_capabilities()
