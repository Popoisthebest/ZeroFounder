from __future__ import annotations

import math
import re
from dataclasses import dataclass

from agents.evidence import evidence_gate
from agents.schemas import Evidence, IdeaCandidate, IdeaEvaluation

TOKEN = re.compile(r"[A-Za-z0-9가-힣]+")


@dataclass(frozen=True)
class SimilarityResult:
    duplicate: bool
    score: float
    method: str
    compared_to: str | None


def tokens(value: str) -> set[str]:
    return {item.lower() for item in TOKEN.findall(value) if len(item) > 1}


def ngrams(value: str, size: int = 3) -> set[str]:
    normalized = " ".join(TOKEN.findall(value.lower()))
    return {normalized[index : index + size] for index in range(max(0, len(normalized) - size + 1))}


def jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 1.0


def structural_text(idea: IdeaCandidate) -> str:
    return " ".join(
        [
            idea.problem_id,
            idea.one_liner,
            " ".join(idea.existing_solutions),
            idea.structural_difference,
            idea.non_ai_value,
            idea.novel_mechanism,
            idea.solution_structure,
        ]
    )


def lexical_similarity(left: IdeaCandidate, right: IdeaCandidate) -> float:
    left_text = structural_text(left)
    right_text = structural_text(right)
    return max(
        jaccard(tokens(left_text), tokens(right_text)),
        jaccard(ngrams(left_text), ngrams(right_text)),
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(item * item for item in left)) * math.sqrt(
        sum(item * item for item in right)
    )
    return (
        sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0.0
    )


def similarity_check(
    candidate: IdeaCandidate,
    corpus: list[IdeaCandidate],
    *,
    lexical_threshold: float,
    semantic_vector: list[float] | None = None,
    corpus_vectors: list[list[float]] | None = None,
    semantic_threshold: float = 0.84,
) -> SimilarityResult:
    for index, prior in enumerate(corpus):
        if (
            candidate.problem_id == prior.problem_id
            and candidate.solution_structure == prior.solution_structure
            and candidate.novel_mechanism.strip().lower() == prior.novel_mechanism.strip().lower()
        ):
            return SimilarityResult(True, 1.0, "structural_fingerprint", prior.idea_id)
        lexical = lexical_similarity(candidate, prior)
        if lexical >= lexical_threshold:
            return SimilarityResult(True, lexical, "lexical", prior.idea_id)
        if semantic_vector is not None and corpus_vectors and index < len(corpus_vectors):
            semantic = cosine_similarity(semantic_vector, corpus_vectors[index])
            if semantic >= semantic_threshold:
                return SimilarityResult(True, semantic, "semantic", prior.idea_id)
    return SimilarityResult(False, 0.0, "none", None)


def validate_candidate_diversity(ideas: list[IdeaCandidate]) -> list[str]:
    problems: list[str] = []
    if not 8 <= len(ideas) <= 10:
        problems.append("candidate count must be between 8 and 10")
    if sum(idea.ai_role == "none" for idea in ideas) < 4:
        problems.append("at least four non-AI ideas are required")
    if sum(idea.ai_role == "assistive" for idea in ideas) > 2:
        problems.append("at most two AI-assistive ideas are allowed")
    if sum(idea.ai_role == "core" for idea in ideas) > 2:
        problems.append("at most two AI-core ideas are allowed")
    if len({user for idea in ideas for user in idea.target_users}) < 4:
        problems.append("at least four target user groups are required")
    if len({idea.solution_structure for idea in ideas}) < 4:
        problems.append("at least four solution structures are required")
    for pattern in ("content", "chatbot", "directory"):
        if sum(idea.product_pattern == pattern for idea in ideas) > 1:
            problems.append(f"at most one {pattern} product is allowed")
    required = {"online_offline", "coordination", "open_data", "workflow_change", "visualization"}
    if not required.issubset({idea.solution_structure for idea in ideas}):
        problems.append("required counter-intuitive solution categories are missing")
    return problems


def eligible_for_selection(
    idea: IdeaCandidate,
    evaluation: IdeaEvaluation,
    evidence_index: dict[str, Evidence],
    *,
    min_business: int = 70,
    min_originality: int = 70,
    max_cliche: int = 39,
    min_evidence: int = 3,
    min_source_types: int = 2,
    min_evidence_quality: float = 0.65,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if idea.idea_id != evaluation.idea_id or evaluation.cliche_review.idea_id != idea.idea_id:
        reasons.append("evaluation identity mismatch")
    if evaluation.business_scores.total < min_business:
        reasons.append("business score below threshold")
    if evaluation.originality_scores.total < min_originality:
        reasons.append("originality score below threshold")
    if (
        evaluation.cliche_review.verdict == "reject"
        or evaluation.cliche_review.cliche_score > max_cliche
    ):
        reasons.append("cliche gate failed")
    if not evaluation.auditor_safe:
        reasons.append("auditor safety gate failed")
    missing = [item for item in idea.evidence_ids if item not in evidence_index]
    if missing:
        reasons.append("unknown evidence ids")
    else:
        passed, evidence_reasons = evidence_gate(
            [evidence_index[item] for item in idea.evidence_ids],
            min_items=min_evidence,
            min_source_types=min_source_types,
            min_quality=min_evidence_quality,
        )
        if not passed:
            reasons.extend(evidence_reasons)
    if not idea.non_ai_value.strip() or idea.ai_role == "core" and len(idea.non_ai_value) < 30:
        reasons.append("non-AI value is insufficient")
    if not idea.first_user_channel or not idea.founder_required_work:
        reasons.append("distribution plan is incomplete")
    return not reasons, reasons


def choose_eligible_idea(
    pairs: list[tuple[IdeaCandidate, IdeaEvaluation]], evidence_index: dict[str, Evidence]
) -> IdeaCandidate | None:
    eligible: list[tuple[float, IdeaCandidate]] = []
    for idea, evaluation in pairs:
        passed, _ = eligible_for_selection(idea, evaluation, evidence_index)
        if passed:
            composite = (
                evaluation.business_scores.total * 0.5
                + evaluation.originality_scores.total * 0.3
                + evaluation.confidence * 100 * 0.2
            )
            eligible.append((composite, idea))
    return max(eligible, key=lambda item: item[0])[1] if eligible else None
