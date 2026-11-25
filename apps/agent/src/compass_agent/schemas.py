from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class StrictModel(BaseModel):
    """Public DTO base: unknown fields are rejected at every boundary."""

    model_config = ConfigDict(extra="forbid")


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    service: Literal["compass-agent"] = "compass-agent"


class DemoOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    source: Literal["synthetic-fixture"] = "synthetic-fixture"
    currency: Literal["CNY"] = "CNY"
    start_date: date
    end_date: date
    revenue_minor: int = Field(ge=0)
    orders: int = Field(ge=0)
    days: int = Field(ge=1)


class PlatformScope(StrictModel):
    kind: Literal["platform"]


class TenantScope(StrictModel):
    kind: Literal["tenant"]
    tenant_id: UUID
    workspace_id: UUID


class TrustedContext(StrictModel):
    actor_user_id: UUID
    scope: Annotated[PlatformScope | TenantScope, Field(discriminator="kind")]
    purpose: Literal["manage", "test", "chat", "module_read"]


class Capability(StrictModel):
    state: Literal["supported", "unsupported", "unknown"]
    reason: str = ""


class ModelCapabilities(StrictModel):
    sync: Capability
    stream: Capability
    tools: Capability
    thinking: Capability
    json_output: Capability
    schema_output: Capability
    reasoning_efforts: list[str] = Field(default_factory=list)


class CatalogItem(StrictModel):
    id: str
    vendor: str
    api_model: str
    kind: Literal["llm", "decision", "unsupported"]
    adapter_supported: bool
    chat_supported: bool = False
    source: Literal["litellm", "project_verified", "systemone"]
    base_url: str | None = None
    capabilities: ModelCapabilities


class CatalogPage(StrictModel):
    items: list[CatalogItem]
    total: int
    page: int
    page_size: int
    sdk_version: str


class ProviderCreate(StrictModel):
    operation_id: UUID
    catalog_id: str = Field(min_length=1, max_length=200)
    api_model: str = Field(min_length=1, max_length=200)
    alias: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=2048)
    api_key: str = Field(default="", max_length=4096, repr=False)
    connection_params: dict | None = Field(default=None, repr=False)
    enabled: bool = False
    chat_visible: bool = False


class ProviderPatch(StrictModel):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    catalog_id: str | None = Field(default=None, min_length=1, max_length=200)
    api_model: str | None = Field(default=None, min_length=1, max_length=200)
    alias: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    connection_params: dict | None = Field(default=None, repr=False)
    enabled: bool | None = None
    chat_visible: bool | None = None


class SavedProvider(StrictModel):
    provider_id: UUID
    version: int
    operation_id: UUID


class TestSummary(StrictModel):
    call_id: UUID
    status: str
    binding_version: int
    started_at: datetime
    capabilities: list[str] = Field(default_factory=list)
    stale: bool = False


class ProviderView(StrictModel):
    id: UUID
    catalog_id: str
    vendor: str
    api_model: str
    alias: str
    kind: Literal["llm", "decision"]
    base_url: str
    has_api_key: bool
    has_connection_params: bool = False
    enabled: bool
    chat_visible: bool
    chat_supported: bool
    version: int
    binding_version: int
    capabilities: ModelCapabilities
    latest_test: TestSummary | None = None
    created_at: datetime
    updated_at: datetime


class ProviderPage(StrictModel):
    items: list[ProviderView]
    total: int
    page: int
    page_size: int


class AvailableModel(StrictModel):
    id: UUID
    alias: str
    api_model: str
    kind: Literal["llm"]
    capabilities: ModelCapabilities


class AvailableModels(StrictModel):
    items: list[AvailableModel]


class ModelChangeView(StrictModel):
    operation_id: UUID
    provider_id: UUID
    actor_user_id: UUID
    before_version: int | None
    after_version: int
    changed_fields: list[str]
    created_at: datetime


class ModelChangePage(StrictModel):
    items: list[ModelChangeView]
    total: int
    page: int
    page_size: int


class ToolFunction(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    description: str = Field(default="", max_length=2000)
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolDefinition(StrictModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolCallFunction(StrictModel):
    name: str
    arguments: str


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_fields(self):
        if self.role == "tool" and (not self.tool_call_id or self.content is None):
            raise ValueError("tool message requires content and tool_call_id")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("tool_call_id requires tool role")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("tool_calls requires assistant role")
        if self.content is None and not self.tool_calls:
            raise ValueError("message requires content or tool calls")
        return self


class GenerateInput(StrictModel):
    kind: Literal["generate"] = "generate"
    messages: list[ChatMessage] = Field(min_length=1, max_length=64)
    stream: bool = False
    thinking: bool = False
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=16)
    output_format: Literal["text", "json", "schema"] = "text"
    output_schema: dict | None = None
    max_tokens: int = Field(default=8192, ge=1, le=32768)
    continuation_token: str | None = Field(default=None, max_length=1048576, repr=False)

    @model_validator(mode="after")
    def validate_options(self):
        if not self.thinking and self.reasoning_effort is not None:
            raise ValueError("reasoning_effort requires thinking")
        if (self.output_format == "schema") != (self.output_schema is not None):
            raise ValueError("output_schema is required only for schema mode")
        return self


class DecisionQuestion(StrictModel):
    type: Literal["choice", "noul", "score"]
    instructions: str | dict | list = ""
    criteria: dict[str, str | dict | list | None] | list[str | dict | list] | None = None

    @model_validator(mode="after")
    def validate_criteria(self):
        if self.type == "choice" and (
            not isinstance(self.criteria, dict) or not 1 <= len(self.criteria) <= 255
        ):
            raise ValueError("choice requires 1..255 named criteria")
        if self.type == "score" and (
            not isinstance(self.criteria, list) or not 2 <= len(self.criteria) <= 255
        ):
            raise ValueError("score requires 2..255 ordered criteria")
        if (
            self.type == "noul"
            and self.criteria is not None
            and (not isinstance(self.criteria, dict) or set(self.criteria) != {"true", "false"})
        ):
            raise ValueError("noul criteria must contain true and false")
        return self


class DecisionInput(StrictModel):
    kind: Literal["decision"] = "decision"
    state: str | dict | list
    questions: dict[str, DecisionQuestion] = Field(min_length=1, max_length=16)
    stream: Literal[False] = False


class TestCallInput(StrictModel):
    call_id: UUID
    input: Annotated[GenerateInput | DecisionInput, Field(discriminator="kind")]


class MainModelSelection(StrictModel):
    mode: Literal["fixed", "auto"]
    provider_id: UUID | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.mode == "fixed") != (self.provider_id is not None):
            raise ValueError("fixed requires provider_id; auto must omit it")
        return self


class ChatCallInput(StrictModel):
    call_id: UUID
    provider_id: UUID | None = None
    main_model: MainModelSelection | None = None
    input: GenerateInput

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.provider_id is None) == (self.main_model is None):
            raise ValueError("provide either provider_id or main_model")
        return self

    def selection(self) -> MainModelSelection:
        return self.main_model or MainModelSelection(mode="fixed", provider_id=self.provider_id)


class ModelUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class OutputValidation(StrictModel):
    status: Literal["not_requested", "valid", "invalid"] = "not_requested"
    mode: Literal["text", "json", "schema", "decision"] = "text"
    paths: list[str] = Field(default_factory=list)


class DecisionAnswer(StrictModel):
    type: Literal["choice", "noul", "score"]
    choice: str | None = None
    noul: float | None = Field(default=None, ge=0, le=1)
    score: float | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    legend: dict[str, str | dict | list] | None = None


class CallOutcome(StrictModel):
    call_id: UUID
    provider_id: UUID
    config_version: int
    status: Literal["completed", "failed", "canceled", "incomplete"]
    duration_ms: int
    usage: ModelUsage = Field(default_factory=ModelUsage)
    error_code: str | None = None
    upstream_outcome_unknown: bool = False
    requested_model: str
    response_model: str | None = None
    upstream_request_id: str | None = None
    finish_reason: str | None = None
    validation: OutputValidation = Field(default_factory=OutputValidation)


class CallResponse(CallOutcome):
    kind: Literal["generate", "decision"]
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    answers: dict[str, DecisionAnswer] | None = None
    continuation_token: str | None = Field(default=None, repr=False)


class Intent(StrictModel):
    category_id: str = Field(max_length=80)
    scene_id: str = Field(max_length=20)


class RoutingFeatures(StrictModel):
    task_type: str = Field(min_length=1, max_length=80)
    difficulty: Literal["L1", "L2", "L3", "undetermined"]
    response_mode: Literal["answer", "clarify", "refuse", "handoff"]
    required_capabilities: list[str] = Field(default_factory=list, max_length=8)


class ModelRoute(StrictModel):
    mode: Literal["fixed", "auto"]
    provider_id: UUID
    model: str
    reason: str
    dictionary_version: str
    routing_version: str


class RoutingMetrics(StrictModel):
    classifier_source: Literal["jev", "flash"] | None = None
    fallback_reason: str | None = None
    classification_calls: list[CallOutcome] = Field(default_factory=list, max_length=2)
    duration_ms: int = 0
    usage_complete: bool = False


class RoutedCallResponse(StrictModel):
    call_id: UUID
    status: Literal["completed", "failed", "canceled", "incomplete"]
    error_code: str | None = None
    intent: Intent | None = None
    features: RoutingFeatures | None = None
    routing: ModelRoute | None = None
    result: CallResponse | None = None
    metrics: RoutingMetrics = Field(default_factory=RoutingMetrics)


class CallRecordView(StrictModel):
    call_id: UUID
    provider_id: UUID
    config_version: int
    binding_version: int
    purpose: Literal["test", "chat"]
    status: Literal["running", "completed", "failed", "canceled", "incomplete"]
    requested_model: str
    response_model: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    usage: ModelUsage
    error_code: str | None
    upstream_outcome_unknown: bool


class StartEvent(StrictModel):
    type: Literal["start"] = "start"
    call_id: UUID
    provider_id: UUID
    config_version: int


class TextDeltaEvent(StrictModel):
    type: Literal["text_delta"] = "text_delta"
    call_id: UUID
    text: str


class ToolDeltaEvent(StrictModel):
    type: Literal["tool_delta"] = "tool_delta"
    call_id: UUID
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


class ReasoningMetaEvent(StrictModel):
    type: Literal["reasoning_meta"] = "reasoning_meta"
    call_id: UUID
    thinking: bool
    reasoning_effort: str | None = None


class FinalEvent(CallOutcome):
    type: Literal["final"] = "final"
    continuation_token: str | None = Field(default=None, repr=False)


class ModelEvent(
    RootModel[
        Annotated[
            StartEvent | TextDeltaEvent | ToolDeltaEvent | ReasoningMetaEvent | FinalEvent,
            Field(discriminator="type"),
        ]
    ]
):
    pass


class RoutingStartEvent(StrictModel):
    type: Literal["start"] = "start"
    call_id: UUID


class ClassificationEvent(StrictModel):
    type: Literal["classification_status"] = "classification_status"
    call_id: UUID
    stage: Literal["jev", "flash"]


class RouteSelectedEvent(StrictModel):
    type: Literal["route_selected"] = "route_selected"
    call_id: UUID
    intent: Intent
    features: RoutingFeatures
    routing: ModelRoute


class RoutingFinalEvent(RoutedCallResponse):
    type: Literal["final"] = "final"


class RoutedModelEvent(
    RootModel[
        Annotated[
            RoutingStartEvent
            | ClassificationEvent
            | RouteSelectedEvent
            | TextDeltaEvent
            | ToolDeltaEvent
            | ReasoningMetaEvent
            | RoutingFinalEvent,
            Field(discriminator="type"),
        ]
    ]
):
    pass


class ModelErrorDetail(StrictModel):
    code: str
    message: str
    requestId: str
    currentVersion: int | None = None


class ModelErrorResponse(StrictModel):
    error: ModelErrorDetail
