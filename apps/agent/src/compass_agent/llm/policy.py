"""Fixed single-attempt policy and output limits. Pure values only."""

import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class OutputLimits:
    visible_input_bytes: int = 65_536
    schema_bytes: int = 16_384
    visible_output_bytes: int = 1_048_576
    private_reasoning_bytes: int = 1_048_576
    continuation_bytes: int = 1_048_576
    wire_response_bytes: int = 2_097_152
    messages: int = 64
    tools: int = 16


@dataclass(frozen=True, slots=True)
class CallPolicy:
    started_monotonic: float
    deadline_monotonic: float
    max_attempts: Literal[1] = 1
    allow_fallback: Literal[False] = False
    connect_timeout_s: float = 10.0
    cleanup_timeout_s: float = 2.0
    limits: OutputLimits = field(default_factory=OutputLimits)

    def __post_init__(self) -> None:
        if self.max_attempts != 1 or self.allow_fallback is not False:
            raise ValueError("current runtime accepts one attempt without fallback")
        budget = self.deadline_monotonic - self.started_monotonic
        if not 0 < budget <= 180:
            raise ValueError("call duration must be within 180 seconds")

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())
