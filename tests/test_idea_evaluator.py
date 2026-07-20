from datetime import UTC, datetime

from agents.idea_evaluator import (
    choose_eligible_idea,
    eligible_for_selection,
    similarity_check,
    validate_candidate_diversity,
)
from agents.schemas import ClicheReview, Evidence, IdeaCandidate, IdeaEvaluation


def idea(
    index: int, structure: str = "software_tool", ai_role: str = "none", pattern: str = "tool"
) -> IdeaCandidate:
    return IdeaCandidate.model_validate(
        {
            "idea_id": f"idea-{index:03}",
            "name": f"Product {index}",
            "one_liner": f"A concrete workflow replacement mechanism number {index}",
            "problem_id": f"problem-{index:03}",
            "evidence_ids": ["evidence-001", "evidence-002", "evidence-003"],
            "target_users": [f"group-{index}"],
            "existing_solutions": ["spreadsheet and group chat"],
            "core_features": ["working feature"],
            "competitors": [],
            "differentiation": "Replaces reconciliation with a shared decision boundary",
            "first_user_channel": "A named local association with posting permission",
            "search_phrases": ["manual reconciliation template"],
            "switching_reason": "Removes a recurring handoff instead of adding another dashboard",
            "founder_required_work": ["Post the approved message manually"],
            "revenue_model": "Optional paid team export later",
            "free_operation": "All MVP computation and storage remain inside the browser",
            "mvp_scope": ["One complete workflow"],
            "difficulty": "low",
            "risks": ["Users may keep their spreadsheet"],
            "kill_criteria": ["No repeat use after ten trials"],
            "cliche_patterns": [],
            "structural_difference": f"Unique structural mechanism {index} changes the handoff",
            "non_ai_value": "The shared rule and visual state work fully without model calls",
            "novel_mechanism": f"Mechanism {index} converts handoffs into a visible constraint",
            "why_now": (
                "Browser capabilities and open formats now make local-first delivery practical"
            ),
            "copy_risk": "Low because the workflow mechanism is specific",
            "ai_role": ai_role,
            "solution_structure": structure,
            "product_pattern": pattern,
        }
    )


def evaluation(item: IdeaCandidate) -> IdeaEvaluation:
    return IdeaEvaluation.model_validate(
        {
            "idea_id": item.idea_id,
            "business_scores": {
                "severity": 12,
                "frequency": 8,
                "user_clarity": 8,
                "solution_gap": 8,
                "free_mvp": 13,
                "differentiation": 8,
                "user_access": 8,
                "revenue_potential": 7,
                "maintainability": 5,
                "safety": 5,
            },
            "originality_scores": {
                "pattern_difference": 16,
                "problem_specificity": 12,
                "mechanism_originality": 16,
                "behavior_change": 12,
                "structural_difference": 12,
                "low_ai_dependency": 10,
                "memorability": 4,
            },
            "cliche_review": {
                "idea_id": item.idea_id,
                "verdict": "pass",
                "cliche_score": 20,
                "reasons": ["Distinct mechanism"],
                "required_changes": [],
            },
            "rationale": ["Evidence-backed"],
            "confidence": 0.8,
            "unverified_assumptions": [],
            "biggest_failure_mode": "No behavior change",
            "mvp_hypothesis": "Users complete the flow twice",
            "success_metrics": ["Repeat completion"],
            "auditor_safe": True,
        }
    )


def evidence_index():
    result = {}
    for index, source in enumerate(["github_issue", "industry_rss", "user_research"], start=1):
        result[f"evidence-{index:03}"] = Evidence(
            evidence_id=f"evidence-{index:03}",
            signal_id=f"signal-{index:03}",
            source_type=source,
            url=f"https://example.com/{index}",
            collected_at=datetime.now(UTC),
            summary="Concrete repeated problem",
            duplicate_cluster=f"cluster-{index}",
            recency_score=0.9,
            source_reliability=0.9,
            specificity_score=0.9,
            directness_score=0.9,
            quality_score=0.9,
        )
    return result


def test_selection_gate_and_choice():
    item = idea(1)
    passed, reasons = eligible_for_selection(item, evaluation(item), evidence_index())
    assert passed, reasons
    assert choose_eligible_idea([(item, evaluation(item))], evidence_index()) == item


def test_cliche_rejection():
    item = idea(1)
    score = evaluation(item)
    score.cliche_review = ClicheReview(
        idea_id=item.idea_id, verdict="reject", cliche_score=85, reasons=["Generic wrapper"]
    )
    passed, reasons = eligible_for_selection(item, score, evidence_index())
    assert not passed
    assert "cliche gate failed" in reasons


def test_structural_duplicate_is_rejected():
    first = idea(1)
    renamed = first.model_copy(update={"idea_id": "idea-999", "name": "Renamed Product"})
    result = similarity_check(renamed, [first], lexical_threshold=0.72)
    assert result.duplicate
    assert result.method == "structural_fingerprint"


def test_diversity_rules():
    structures = [
        "online_offline",
        "coordination",
        "open_data",
        "workflow_change",
        "visualization",
        "software_tool",
        "information_product",
        "community_participation",
    ]
    ideas = [idea(index + 1, structure=structure) for index, structure in enumerate(structures)]
    assert validate_candidate_diversity(ideas) == []
