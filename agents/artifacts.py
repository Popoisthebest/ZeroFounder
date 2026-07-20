from __future__ import annotations

from agents.schemas import Evidence, IdeaCandidate, IdeaEvaluation


def render_selection_decision(
    selected: IdeaCandidate,
    evaluation: IdeaEvaluation,
    evidence_index: dict[str, Evidence],
    rejected: list[tuple[str, list[str]]],
) -> str:
    evidence_lines = []
    for evidence_id in selected.evidence_ids:
        evidence = evidence_index[evidence_id]
        evidence_lines.append(f"- [{evidence_id}]({evidence.url}) — {evidence.summary}")
    rejected_lines = [f"- {idea_id}: {', '.join(reasons)}" for idea_id, reasons in rejected] or [
        "- 없음"
    ]
    return "\n".join(
        [
            f"# {selected.name} 선정 결정",
            "",
            selected.one_liner,
            "",
            "## 검증된 근거",
            "",
            *evidence_lines,
            "",
            "## 평가",
            "",
            f"- 사업성: {evaluation.business_scores.total}/100",
            f"- 독창성: {evaluation.originality_scores.total}/100",
            f"- 클리셰: {evaluation.cliche_review.cliche_score}/100",
            f"- 비-AI 핵심 가치: {selected.non_ai_value}",
            f"- 구조적 차이: {selected.structural_difference}",
            "",
            "## 탈락 후보",
            "",
            *rejected_lines,
            "",
        ]
    )
