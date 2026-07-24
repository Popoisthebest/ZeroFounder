import pytest

import agents.pdf_report as pdf_report
from agents.pdf_report import (
    ReportPdfError,
    ReportPdfSource,
    assert_clean_unicode_strings,
    build_report_pdf,
    contains_mojibake,
    extract_pdf_text,
    validate_report_pdf_bytes,
)


def _source(*, long: bool = False) -> ReportPdfSource:
    paragraph = (
        "재고 관리 시스템에서 목록 탐색 비효율성이 반복됩니다. "
        "재고 관리자와 창고 운영자는 5 또는 10줄 단위로 이동하기를 원합니다. "
    )
    if long:
        paragraph *= 120
    return ReportPdfSource(
        title="ZeroFounder 주간 운영 보고서",
        problem_id="problem-navigation-inefficiency",
        problem_title="목록 탐색 비효율성",
        problem_domain="재고 관리 시스템",
        target_users="재고 관리자, 창고 운영자",
        problem_summary="긴 재고 목록에서 특정 위치나 항목으로 이동하기 어렵습니다.",
        report_period="2026-W30",
        report_type="weekly",
        period_summary=paragraph,
        sections=[("핵심 판단", paragraph)],
        findings=["재고 관리자와 창고 운영자가 위치 복귀에 시간을 씁니다."],
        recommendations=["5 또는 10줄 단위로 이동하는 실험을 준비합니다."],
        evidence_ids=["signal-d38b3b6e3daaa7fc"],
        required_text=[
            "problem-navigation-inefficiency",
            "2026-W30",
            "signal-d38b3b6e3daaa7fc",
            "재고 관리 시스템",
        ],
    )


def test_korean_source_strings_round_trip_as_unicode():
    samples = [
        "재고 관리 시스템",
        "재고 관리자",
        "창고 운영자",
        "목록 탐색 비효율성",
        "5 또는 10줄 단위로 이동",
    ]

    assert_clean_unicode_strings(samples)
    for sample in samples:
        assert sample.encode("utf-8").decode("utf-8") == sample


def test_mojibake_signatures_are_detected_without_rejecting_normal_text():
    assert contains_mojibake("ìž¬ê³  ê´€ë¦¬ ì‹œìŠ¤í…œ")
    assert contains_mojibake("Œ º")
    assert contains_mojibake("깨진 문자 � 포함")
    assert not contains_mojibake("재고 관리 시스템 ABC 123 signal-001")


def test_mojibake_fails_before_pdf_generation():
    with pytest.raises(ReportPdfError, match="mojibake"):
        assert_clean_unicode_strings(["ìž¬ê³  ê´€ë¦¬ ì‹œìŠ¤í…œ"])


def test_missing_korean_font_fails_in_github_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("ZEROFOUNDER_REPORT_FONT_PATH", raising=False)
    monkeypatch.setattr(pdf_report, "_fc_match", lambda _family: None)

    with pytest.raises(ReportPdfError) as exc_info:
        pdf_report.register_report_font()
    assert exc_info.value.code == "korean_font_not_installed"


def test_configured_missing_font_path_has_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROFOUNDER_REPORT_FONT_PATH", str(tmp_path / "missing.ttf"))

    with pytest.raises(ReportPdfError) as exc_info:
        pdf_report.register_report_font()
    assert exc_info.value.code == "korean_font_not_found"


def test_find_korean_font_uses_fontconfig_path(monkeypatch, tmp_path):
    font = tmp_path / "NanumGothic.ttf"
    font.write_bytes(b"not a real font")
    monkeypatch.delenv("ZEROFOUNDER_REPORT_FONT_PATH", raising=False)
    monkeypatch.setattr(
        pdf_report,
        "_fc_match",
        lambda family: font if family == "NanumGothic" else None,
    )

    detection = pdf_report.detect_korean_font()

    assert pdf_report.find_korean_font() == str(font)
    assert detection.path == str(font)
    assert detection.family == "NanumGothic"
    assert detection.format == "ttf"
    assert detection.exists is True
    assert detection.size == len(b"not a real font")


def test_register_report_font_accepts_configured_nanum_path(monkeypatch, tmp_path):
    font = tmp_path / "NanumGothic.ttf"
    font.write_bytes(b"fake font")
    monkeypatch.setenv("ZEROFOUNDER_REPORT_FONT_PATH", str(font))
    monkeypatch.setattr(
        pdf_report,
        "_register_ttf_font",
        lambda path: ("ZeroFounderKoreanTest", True),
    )

    assert pdf_report.register_report_font() == ("ZeroFounderKoreanTest", True)


def test_strict_font_pdf_generation_with_installed_ttf(monkeypatch):
    font_path = pdf_report.find_korean_font()
    if font_path is None:
        pytest.skip("Nanum/Noto Korean TTF is not installed in this environment")
    monkeypatch.setenv("ZEROFOUNDER_PDF_STRICT_FONT", "1")

    source = _source()
    pdf = build_report_pdf(source)
    validation = validate_report_pdf_bytes(
        pdf,
        required_text=source.required_text,
        require_embedded_font=True,
    )

    assert validation.status == "valid"
    assert validation.fonts_embedded is True
    assert validation.extracted_text_check == "passed"
    assert validation.mojibake_detected is False
    assert validation.render_check in {"passed", "not_available"}


def test_unicode_pdf_generation_extracts_korean_and_evidence_ids():
    source = _source()
    pdf = build_report_pdf(source)
    text = extract_pdf_text(pdf)

    assert "재고 관리 시스템" in text
    assert "재고 관리자, 창고 운영자" in text
    assert "signal-d38b3b6e3daaa7fc" in text
    validation = validate_report_pdf_bytes(
        pdf,
        required_text=source.required_text,
        require_embedded_font=False,
    )
    assert validation.status == "valid"
    assert validation.page_count >= 1
    assert validation.extracted_text_check == "passed"
    assert validation.render_check in {"passed", "not_available"}


def test_long_korean_report_wraps_and_can_span_multiple_pages():
    source = _source(long=True)
    pdf = build_report_pdf(source)
    validation = validate_report_pdf_bytes(
        pdf,
        required_text=source.required_text,
        require_embedded_font=False,
    )

    assert validation.status == "valid"
    assert validation.page_count >= 2
