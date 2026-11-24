"""Application-level model configuration types.

This module only defines the hand-off value object; it never imports the service,
repository, FastAPI or SQLAlchemy modules.
"""

from dataclasses import dataclass, field
from datetime import datetime

from compass_agent.llm.contracts import CallPolicy, InvocationContext, ModelSpec


@dataclass(frozen=True, slots=True)
class PreparedCall:
    spec: ModelSpec
    context: InvocationContext
    policy: CallPolicy
    started_at: datetime
    deadline_at: datetime
    ciphertext: str = field(repr=False)


__all__ = ["PreparedCall"]
