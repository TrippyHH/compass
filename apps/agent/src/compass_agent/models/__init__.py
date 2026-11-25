"""Application-level model configuration types.

This module only defines the hand-off value object; it never imports the service,
repository, FastAPI or SQLAlchemy modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from compass_agent.llm.contracts import CallPolicy, InvocationContext, ModelSpec
from compass_agent.schemas import PlatformScope, TenantScope


@dataclass(frozen=True, slots=True)
class PreparedCall:
    spec: ModelSpec
    context: InvocationContext
    policy: CallPolicy
    started_at: datetime
    deadline_at: datetime
    ciphertext: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RoutingGrant:
    """In-process authorization for one internal classification call.

    The browser cannot submit this value: it is derived from the trusted context and
    the server-side routing policy, and it never grants data or tool access.
    """

    actor_user_id: UUID
    scope: PlatformScope | TenantScope
    call_id: UUID
    provider_id: UUID
    stage: str


__all__ = ["PreparedCall", "RoutingGrant"]
