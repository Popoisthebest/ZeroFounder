from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

StrictId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LifecycleStage(StrEnum):
    DISCOVERY = "DISCOVERY"
    EVIDENCE_VALIDATION = "EVIDENCE_VALIDATION"
    IDEA_EVALUATION = "IDEA_EVALUATION"
    DISTRIBUTION_CHECK = "DISTRIBUTION_CHECK"
    IDEA_SELECTED = "IDEA_SELECTED"
    FOUNDER_APPROVAL = "FOUNDER_APPROVAL"
    MVP_PLANNING = "MVP_PLANNING"
    INFRASTRUCTURE_SELECTION = "INFRASTRUCTURE_SELECTION"
    MVP_BUILDING = "MVP_BUILDING"
    PRE_LAUNCH = "PRE_LAUNCH"
    DISTRIBUTION_REQUIRED = "DISTRIBUTION_REQUIRED"
    VALIDATION_RUNNING = "VALIDATION_RUNNING"
    OPERATING = "OPERATING"
    GROWTH_EXPERIMENT = "GROWTH_EXPERIMENT"
    PIVOT_REVIEW = "PIVOT_REVIEW"
    PIVOTING = "PIVOTING"
    PAUSED = "PAUSED"


class AgentRole(StrEnum):
    CEO = "ceo"
    MARKET_SCOUT = "market_scout"
    RESEARCHER = "researcher"
    VENTURE_ANALYST = "venture_analyst"
    CLICHE_CRITIC = "cliche_critic"
    PRODUCT_MANAGER = "product_manager"
    BUILDER = "builder"
    DESIGNER = "designer"
    GROWTH_MANAGER = "growth_manager"
    CUSTOMER_ANALYST = "customer_analyst"
    DATA_ANALYST = "data_analyst"
    AUDITOR = "auditor"
    SECRETARY = "secretary"


class ActionType(StrEnum):
    NO_OP = "no_op"
    COLLECT_SIGNALS = "collect_signals"
    CREATE_PROBLEM_CANDIDATE = "create_problem_candidate"
    VALIDATE_EVIDENCE = "validate_evidence"
    CREATE_IDEA_CANDIDATES = "create_idea_candidates"
    EVALUATE_IDEAS = "evaluate_ideas"
    CHECK_DISTRIBUTION = "check_distribution"
    SELECT_IDEA = "select_idea"
    REQUEST_FOUNDER_APPROVAL = "request_founder_approval"
    CREATE_PRODUCT_SPEC = "create_product_spec"
    SELECT_INFRASTRUCTURE = "select_infrastructure"
    CREATE_CODE_PATCH = "create_code_patch"
    CREATE_CONTENT = "create_content"
    CREATE_EXPERIMENT = "create_experiment"
    ANALYZE_FEEDBACK = "analyze_feedback"
    RECORD_VALIDATION = "record_validation"
    UPDATE_STRATEGY = "update_strategy"
    RECOMMEND_PIVOT = "recommend_pivot"
    WRITE_REPORT = "write_report"
    OPEN_ISSUE = "open_issue"
    CREATE_PULL_REQUEST = "create_pull_request"
    UPDATE_STATE = "update_state"
    PROPOSE_DEPENDENCY = "propose_dependency"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InfrastructureProvider(StrEnum):
    UNSELECTED = "unselected"
    GITHUB_PAGES = "github_pages"
    CLOUDFLARE_PAGES = "cloudflare_pages"
    CLOUDFLARE_PAGES_WORKERS_D1 = "cloudflare_pages_workers_d1"


class StateTransition(StrictModel):
    from_stage: LifecycleStage = Field(alias="from")
    to_stage: LifecycleStage = Field(alias="to")


class FileChange(StrictModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=200_000)
    operation: Literal["upsert"] = "upsert"


class DependencyProposal(StrictModel):
    proposal_id: StrictId
    ecosystem: Literal["npm", "python"]
    package_name: str = Field(pattern=r"^(?:@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)$")
    exact_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}$")
    dependency_type: Literal["runtime", "development"]
    reason: str = Field(min_length=10, max_length=2000)
    standard_library_alternative: str = Field(min_length=2, max_length=1000)
    license: str = Field(min_length=1, max_length=100)
    security_risk: str = Field(min_length=2, max_length=1000)
    bundle_or_maintenance_impact: str = Field(min_length=2, max_length=1000)
    requested_by_action: ActionType
    status: Literal["proposed"] = "proposed"


class ActionEnvelope(StrictModel):
    role: AgentRole
    action_type: ActionType
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel
    requires_approval: bool
    evidence_ids: list[StrictId] = Field(default_factory=list, max_length=100)
    state_transition: StateTransition | None = None
    files: list[FileChange] = Field(default_factory=list, max_length=50)
    dependency_proposal: DependencyProposal | None = None

    @model_validator(mode="after")
    def enforce_action_shape(self) -> ActionEnvelope:
        if self.action_type == ActionType.NO_OP and (
            self.files or self.state_transition or self.dependency_proposal
        ):
            raise ValueError("no_op cannot mutate files, dependencies, or state")
        if self.action_type == ActionType.PROPOSE_DEPENDENCY and not self.dependency_proposal:
            raise ValueError("dependency proposal payload is required")
        if self.dependency_proposal and self.action_type != ActionType.PROPOSE_DEPENDENCY:
            raise ValueError("dependency proposal is only valid for propose_dependency")
        return self


class CompanyState(StrictModel):
    lifecycle_stage: LifecycleStage = LifecycleStage.DISCOVERY
    autonomy_level: int = Field(default=1, ge=0, le=2)
    selected_venture: str | None = None
    active_experiment: str | None = None
    infrastructure_provider: InfrastructureProvider = InfrastructureProvider.UNSELECTED
    sleep_mode: bool = False
    consecutive_failures: int = Field(default=0, ge=0)
    last_agent_run: datetime | None = None
    paused_from: LifecycleStage | None = None
    validation_started_at: datetime | None = None
    deployed_at: datetime | None = None


class TriggerReason(StrEnum):
    NEW_SIGNALS = "new_signals"
    STRONG_SIGNAL = "strong_signal"
    NEW_ISSUE = "new_issue"
    APPROVAL_COMMAND = "approval_command"
    PRODUCT_CHANGED = "product_changed"
    METRICS_CHANGED = "metrics_changed"
    EXPERIMENT_DUE = "experiment_due"
    DAILY_REVIEW = "daily_review"
    WEEKLY_REVIEW = "weekly_review"
    MANUAL = "manual"


class RepositoryCheckpoint(StrictModel):
    version: int = 1
    last_signal_ids: list[StrictId] = Field(default_factory=list)
    processed_issue_ids: list[int] = Field(default_factory=list)
    processed_comment_ids: list[int] = Field(default_factory=list)
    idempotency_keys: list[str] = Field(default_factory=list)
    last_product_sha: str | None = None
    last_metrics_hash: str | None = None
    last_daily_review: date | None = None
    last_weekly_review: date | None = None
    updated_at: datetime | None = None


class PreflightDecision(StrictModel):
    should_call_model: bool
    reasons: list[TriggerReason] = Field(default_factory=list)
    new_signal_ids: list[StrictId] = Field(default_factory=list)
    issue_ids: list[int] = Field(default_factory=list)
    comment_ids: list[int] = Field(default_factory=list)
    product_sha: str | None = None
    metrics_hash: str | None = None
    idempotency_key: str


class UsageDay(StrictModel):
    date: date
    chat_calls: int = Field(default=0, ge=0)
    embedding_calls: int = Field(default=0, ge=0)
    catalog_calls: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    request_fingerprints: list[str] = Field(default_factory=list)

    @property
    def inference_calls(self) -> int:
        return self.chat_calls + self.embedding_calls


class UsageLedger(StrictModel):
    days: list[UsageDay] = Field(default_factory=list)


class Evidence(StrictModel):
    evidence_id: StrictId
    signal_id: StrictId
    source_type: str
    url: str
    collected_at: datetime
    published_at: datetime | None = None
    summary: str = Field(min_length=1, max_length=2000)
    duplicate_cluster: str
    recency_score: float = Field(ge=0, le=1)
    source_reliability: float = Field(ge=0, le=1)
    specificity_score: float = Field(ge=0, le=1)
    directness_score: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)

    @field_validator("url")
    @classmethod
    def http_url_only(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("evidence URL must be HTTP(S)")
        return value


class MarketSignal(StrictModel):
    signal_id: StrictId
    source_pack: str
    source_type: str
    url: str
    title: str = Field(max_length=300)
    summary: str = Field(max_length=2000)
    collected_at: datetime
    published_at: datetime | None = None
    content_hash: str


class ProblemCandidate(StrictModel):
    problem_id: StrictId
    title: str
    target_users: list[str]
    description: str
    evidence_ids: list[StrictId]
    frequency_score: int = Field(ge=0, le=10)
    severity_score: int = Field(ge=0, le=10)
    buildability_score: int = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=1)


class DecisionRecord(StrictModel):
    timestamp: datetime
    run_id: str
    lifecycle_stage: LifecycleStage
    role: AgentRole
    action: ActionType
    decision: str
    rationale: str
    result: str


class FounderResult(StrictModel):
    result_id: StrictId
    recorded_by: str
    recorded_at: datetime
    source_type: Literal["human_commit", "verified_issue"]
    evidence_url: str
    activity: str
    outcome: str


class FounderResults(StrictModel):
    records: list[FounderResult] = Field(default_factory=list)
