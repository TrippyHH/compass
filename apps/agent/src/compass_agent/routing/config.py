"""Code-owned routing policy. Provider credentials stay in the existing store."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingPolicy:
    version: str = "lesson12-flow-1"
    intent_probability: float = 0.5
    intent_margin: float = 0.1
    classification_seconds: float = 30
    jev_seconds: float = 8
    flash_seconds: float = 20
    parent_seconds: float = 180


POLICY = RoutingPolicy()
