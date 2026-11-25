"""Read-only scene features; classification never predicts these fields."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files

from compass_agent.models.errors import ModelError
from compass_agent.schemas import Intent, RoutingFeatures

SCENE_COUNT = 40


def _digest(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@lru_cache
def catalog() -> tuple[str, dict[str, dict]]:
    raw = json.loads(files(__package__).joinpath("scenes.json").read_text(encoding="utf-8"))
    payload = {"categories": raw["categories"], "scenes": raw["scenes"]}
    version = str(raw.get("version", ""))
    if version != "sha256:" + _digest(payload):
        raise ValueError("Routing dictionary digest mismatch")
    categories = {row["category_id"]: row["name"] for row in raw["categories"]}
    if len(categories) != len(raw["categories"]):
        raise ValueError("Duplicate routing categories")
    scenes: dict[str, dict] = {}
    for row in raw["scenes"]:
        scene_id = row["scene_id"]
        if scene_id in scenes:
            raise ValueError("Duplicate routing scenes")
        if row["category_id"] not in categories:
            raise ValueError("Unknown routing category")
        if not row["name"].strip() or not row["description"].strip():
            raise ValueError("Routing scene name and description are required")
        features = RoutingFeatures(**{key: row[key] for key in RoutingFeatures.model_fields})
        if set(features.required_capabilities) - {
            "sync",
            "stream",
            "tools",
            "thinking",
            "json_output",
            "schema_output",
        }:
            raise ValueError("Unknown routing capability")
        scenes[scene_id] = {
            **row,
            **features.model_dump(),
            "category_name": categories[row["category_id"]],
        }
    if len(scenes) != SCENE_COUNT:
        raise ValueError("Routing dictionary must contain 40 scenes")
    return version, scenes


def validate_intent(value) -> Intent:
    intent = Intent.model_validate(value)
    scene = catalog()[1].get(intent.scene_id)
    if scene is None or scene["category_id"] != intent.category_id:
        raise ModelError("intent_invalid", "分类结果不在当前场景字典中。")
    return intent


def features_for(intent: Intent) -> RoutingFeatures:
    row = catalog()[1][intent.scene_id]
    return RoutingFeatures(**{key: row[key] for key in RoutingFeatures.model_fields})


def categories() -> dict[str, str]:
    return {row["category_id"]: row["category_name"] for row in catalog()[1].values()}


def descriptions() -> dict[str, str]:
    return {
        key: row["category_name"] + " / " + row["name"] + "：" + row["description"]
        for key, row in catalog()[1].items()
    }


def scenes() -> dict[str, dict]:
    return catalog()[1]
