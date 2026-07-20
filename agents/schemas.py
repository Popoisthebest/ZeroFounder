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


class ActionRejectionCode(StrEnum):
    SLEEP_MODE = "sleep_mode"
    MODEL_CATALOG_UNAVAILABLE = "model_catalog_unavailable"
    NO_COMPATIBLE_MODEL = "no_compatible_model"
    MODEL_RESPONSE_REJECTED = "model_response_rejected"
    LIFECYCLE_ACTION_NOT_ALLOWED = "lifecycle_action_not_allowed"
    STATE_TRANSITION_SOURCE_MISMATCH = "state_transition_source_mismatch"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    EVIDENCE_REFERENCE_REJECTED = "evidence_reference_rejected"


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
        if self.action_type in {
            ActionType.CREATE_PROBLEM_CANDIDATE,
            ActionType.VALIDATE_EVIDENCE,
        }:
            if not self.evidence_ids:
                raise ValueError("discovery analysis actions require stored evidence_ids")
            if not self.files:
                raise ValueError("discovery analysis actions require a material output file")
        return self


class ModelActionDiagnostic(StrictModel):
    lifecycle_stage: LifecycleStage
    allowed_action_types: list[ActionType] = Field(min_length=1)
    original_action_type: ActionType | None = None
    validated_action_type: ActionType
    accepted: bool
    rejection_code: ActionRejectionCode | None = None
    rejection_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def enforce_diagnostic_shape(self) -> ModelActionDiagnostic:
        if self.accepted and (self.rejection_code or self.rejection_reason):
            raise ValueError("accepted diagnostics cannot contain a rejection")
        if not self.accepted and (not self.rejection_code or not self.rejection_reason):
            raise ValueError("rejected diagnostics require a code and reason")
        if (
            self.original_action_type is not None
            and self.accepted
            and self.original_action_type != self.validated_action_type
        ):
            raise ValueError("accepted action type cannot change during validation")
        return self


class ModelRunOutcome(StrictModel):
    action: ActionEnvelope
    diagnostic: ModelActionDiagnostic


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
    blocked_reason: str | None = None


class UsageDay(StrictModel):
    date: date
    chat_calls: int = Field(default=0, ge=0)
    embedding_calls: int = Field(default=0, ge=0)
    catalog_calls: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    inference_call_upper_bound: int = Field(default=0, ge=0)
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


class EvidenceClassification(StrictModel):
    evidence_id: StrictId
    specificity: Literal["low", "medium", "high"]
    directness: Literal["indirect", "mixed", "direct"]


class IdeaCandidate(StrictModel):
    idea_id: StrictId
    name: str = Field(min_length=2, max_length=100)
    one_liner: str = Field(min_length=10, max_length=300)
    problem_id: StrictId
    evidence_ids: list[StrictId] = Field(min_length=1, max_length=20)
    target_users: list[str] = Field(min_length=1, max_length=8)
    existing_solutions: list[str] = Field(min_length=1, max_length=10)
    core_features: list[str] = Field(min_length=1, max_length=3)
    competitors: list[str] = Field(default_factory=list, max_length=10)
    differentiation: str = Field(min_length=10, max_length=1000)
    first_user_channel: str = Field(min_length=5, max_length=1000)
    search_phrases: list[str] = Field(min_length=1, max_length=10)
    switching_reason: str = Field(min_length=10, max_length=1000)
    founder_required_work: list[str] = Field(min_length=1, max_length=10)
    revenue_model: str = Field(min_length=2, max_length=500)
    free_operation: str = Field(min_length=10, max_length=1000)
    mvp_scope: list[str] = Field(min_length=1, max_length=3)
    difficulty: Literal["low", "medium", "high"]
    risks: list[str] = Field(min_length=1, max_length=10)
    kill_criteria: list[str] = Field(min_length=1, max_length=10)
    cliche_patterns: list[str] = Field(default_factory=list, max_length=10)
    structural_difference: str = Field(min_length=10, max_length=1000)
    non_ai_value: str = Field(min_length=10, max_length=1000)
    novel_mechanism: str = Field(min_length=10, max_length=1000)
    why_now: str = Field(min_length=10, max_length=1000)
    copy_risk: str = Field(min_length=2, max_length=500)
    ai_role: Literal["none", "assistive", "core"]
    solution_structure: Literal[
        "software_tool",
        "information_product",
        "community_participation",
        "workflow_change",
        "coordination",
        "visualization",
        "open_data",
        "online_offline",
    ]
    product_pattern: Literal["tool", "content", "chatbot", "directory", "data", "coordination"]


class BusinessScores(StrictModel):
    severity: int = Field(ge=0, le=15)
    frequency: int = Field(ge=0, le=10)
    user_clarity: int = Field(ge=0, le=10)
    solution_gap: int = Field(ge=0, le=10)
    free_mvp: int = Field(ge=0, le=15)
    differentiation: int = Field(ge=0, le=10)
    user_access: int = Field(ge=0, le=10)
    revenue_potential: int = Field(ge=0, le=10)
    maintainability: int = Field(ge=0, le=5)
    safety: int = Field(ge=0, le=5)

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class OriginalityScores(StrictModel):
    pattern_difference: int = Field(ge=0, le=20)
    problem_specificity: int = Field(ge=0, le=15)
    mechanism_originality: int = Field(ge=0, le=20)
    behavior_change: int = Field(ge=0, le=15)
    structural_difference: int = Field(ge=0, le=15)
    low_ai_dependency: int = Field(ge=0, le=10)
    memorability: int = Field(ge=0, le=5)

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class ClicheReview(StrictModel):
    idea_id: StrictId
    verdict: Literal["reject", "pass"]
    cliche_score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(min_length=1, max_length=20)
    required_changes: list[str] = Field(default_factory=list, max_length=20)


class IdeaEvaluation(StrictModel):
    idea_id: StrictId
    business_scores: BusinessScores
    originality_scores: OriginalityScores
    cliche_review: ClicheReview
    rationale: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    unverified_assumptions: list[str] = Field(default_factory=list, max_length=20)
    biggest_failure_mode: str
    mvp_hypothesis: str
    success_metrics: list[str] = Field(min_length=1, max_length=10)
    auditor_safe: bool


class SignalSource(StrictModel):
    source_id: StrictId
    source_type: str
    adapter: Literal[
        "github_search",
        "github_repo_search",
        "github_discussions",
        "hacker_news",
        "rss",
        "repository_issues",
        "inbox",
    ]
    enabled: bool = True
    url: str | None = None
    query: str | None = None
    repositories: list[str] = Field(default_factory=list)
    reliability: float = Field(ge=0, le=1)
    max_items: int = Field(default=25, ge=1, le=100)
    terms_note: str | None = Field(default=None, max_length=500)
    robots_note: str | None = Field(default=None, max_length=500)


class SignalPack(StrictModel):
    pack_id: StrictId
    enabled: bool = True
    sources: list[SignalSource]


class SignalSourceConfig(StrictModel):
    enabled_packs: list[StrictId]
    packs: list[SignalPack]


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


class ExperimentStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class Experiment(StrictModel):
    experiment_id: StrictId
    hypothesis: str = Field(min_length=10, max_length=2000)
    change: str = Field(min_length=5, max_length=2000)
    target_metric: str = Field(min_length=2, max_length=500)
    success_condition: str = Field(min_length=5, max_length=1000)
    failure_condition: str = Field(min_length=5, max_length=1000)
    start_date: date
    review_date: date
    status: ExperimentStatus = ExperimentStatus.PLANNED

    @model_validator(mode="after")
    def valid_dates(self) -> Experiment:
        if self.review_date < self.start_date:
            raise ValueError("experiment review date precedes start date")
        return self


class ValidationThresholds(StrictModel):
    validation_period_days: int = Field(default=14, ge=1)
    min_distribution_activities: int = Field(default=2, ge=1)
    min_user_or_visit_signals: int = Field(default=10, ge=1)
    min_feedback_items: int = Field(default=3, ge=1)
    min_growth_experiments: int = Field(default=2, ge=1)
    min_distinct_feedback_authors: int = Field(default=2, ge=1)


class ValidationSnapshot(StrictModel):
    validation_days: int = Field(ge=0)
    distribution_activities: int = Field(ge=0)
    user_or_visit_signals: int = Field(ge=0)
    feedback_items: int = Field(ge=0)
    growth_experiments: int = Field(ge=0)
    distinct_feedback_authors: int = Field(ge=0)
    feedback_paths_verified: bool
    active_experiment: bool
    failure_indicators: list[str] = Field(default_factory=list)
