from __future__ import annotations

import io
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

MOJIBAKE_PATTERNS = (
    re.compile(r"[ìŒº]{2,}"),
    re.compile(r"(?:ì|í|ë|ê|ã|Â|Ã|Ð|Ñ)[\x80-\xffA-Za-z]{1,}"),
    re.compile(r"[\u0080-\u009f]"),
)
REPLACEMENT_CHARACTER = "\ufffd"
KOREAN_SAMPLE_TEXT = [
    "재고 관리 시스템",
    "재고 관리자",
    "창고 운영자",
    "목록 탐색 비효율성",
    "5 또는 10줄 단위로 이동",
]
FONT_ENV = "ZEROFOUNDER_REPORT_FONT_PATH"
STRICT_FONT_ENVS = ("ZEROFOUNDER_PDF_STRICT_FONT", "ZEROfOUNDER_PDF_STRICT_FONT")
CID_FALLBACK_FONT = "HYSMyeongJo-Medium"


class ReportPdfError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PdfValidationResult:
    status: str
    rejection_code: str = ""
    rejection_reason: str = ""
    font_names: tuple[str, ...] = ()
    fonts_embedded: bool = False
    unicode_mapping_available: bool = False
    extracted_text_check: str = "not_checked"
    mojibake_detected: bool = False
    required_text_found: bool = False
    render_check: str = "not_checked"
    page_count: int = 0
    missing_required_text: tuple[str, ...] = ()

    def model_summary(self) -> dict[str, Any]:
        return {
            "pdf_validation_status": self.status,
            "pdf_font_names": list(self.font_names),
            "pdf_fonts_embedded": self.fonts_embedded,
            "pdf_unicode_mapping_available": self.unicode_mapping_available,
            "extracted_text_check": self.extracted_text_check,
            "mojibake_detected": self.mojibake_detected,
            "required_text_found": self.required_text_found,
            "render_check": self.render_check,
            "page_count": self.page_count,
        }


@dataclass(frozen=True)
class ReportPdfSource:
    title: str
    problem_id: str
    problem_title: str
    problem_domain: str
    target_users: str
    problem_summary: str
    report_period: str
    report_type: str
    period_summary: str
    sections: list[tuple[str, str]]
    findings: list[str]
    recommendations: list[str]
    evidence_ids: list[str]
    operation_key: str | None = None
    operation_key_hash: str | None = None
    required_text: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KoreanFontDetection:
    path: str | None = None
    family: str = ""
    format: str = ""
    exists: bool = False
    size: int = 0
    source: str = ""

    def model_summary(self) -> dict[str, Any]:
        return {
            "detected_font_path": self.path or "",
            "detected_font_family": self.family,
            "detected_font_format": self.format,
            "font_file_exists": self.exists,
            "font_file_size": self.size,
        }


def contains_mojibake(value: str) -> bool:
    if REPLACEMENT_CHARACTER in value:
        return True
    if "Œ" in value or "º" in value:
        return True
    return any(pattern.search(value) for pattern in MOJIBAKE_PATTERNS)


def assert_clean_unicode_strings(values: list[str]) -> None:
    for value in values:
        if not isinstance(value, str):
            raise ReportPdfError("invalid_report_text_encoding", "report text is not str")
        if contains_mojibake(value):
            raise ReportPdfError("mojibake_detected", "report text contains mojibake")


def _fc_match(family: str) -> Path | None:
    try:
        result = subprocess.run(
            ["fc-match", "--format=%{file}", family],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    path = result.stdout.strip()
    return Path(path) if path else None


def _font_detection(path: Path, *, family: str, source: str) -> KoreanFontDetection:
    exists = path.exists()
    return KoreanFontDetection(
        path=str(path),
        family=family,
        format=path.suffix.lower().lstrip("."),
        exists=exists,
        size=path.stat().st_size if exists else 0,
        source=source,
    )


def detect_korean_font() -> KoreanFontDetection:
    configured = os.getenv(FONT_ENV)
    if configured:
        return _font_detection(Path(configured), family="configured", source=FONT_ENV)
    for family in ("NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"):
        matched = _fc_match(family)
        if matched and any(token in matched.name.lower() for token in ("nanum", "noto")):
            return _font_detection(matched, family=family, source="fontconfig")
    candidates = [
        (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"), "NanumGothic"),
        (Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"), "NanumGothic"),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), "Noto Sans CJK KR"),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf"), "Noto Sans KR"),
    ]
    for path, family in candidates:
        if path.exists():
            return _font_detection(path, family=family, source="known_path")
    return KoreanFontDetection(family="NanumGothic")


def discover_korean_font() -> Path | None:
    detection = detect_korean_font()
    if detection.path and detection.exists:
        return Path(detection.path)
    return None


def find_korean_font() -> str | None:
    font = discover_korean_font()
    return str(font) if font else None


def strict_font_required() -> bool:
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        return True
    return any(
        os.getenv(name, "").strip() in {"1", "true", "TRUE", "yes"}
        for name in STRICT_FONT_ENVS
    )


def _verify_korean_glyphs(font_name: str) -> None:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont(font_name, 10)
    pdf.drawString(20 * mm, 270 * mm, "재고 관리 시스템")
    pdf.save()
    text = extract_pdf_text(buffer.getvalue())
    if "재고 관리 시스템" not in text:
        raise ReportPdfError(
            "korean_font_missing_glyphs",
            "registered Korean font failed Hangul text extraction check",
        )


def _register_ttf_font(font_path: Path) -> tuple[str, bool]:
    if not font_path.exists():
        raise ReportPdfError(
            "korean_font_not_found",
            f"Korean font path does not exist: {font_path}",
        )
    if font_path.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
        raise ReportPdfError(
            "korean_font_not_found",
            f"Korean font path is not TTF/TTC/OTF: {font_path}",
        )
    candidates: list[Path] = [font_path]
    for path in candidates:
        last_error: Exception | None = None
        for index in range(16 if path.suffix.lower() == ".ttc" else 1):
            name = f"ZeroFounderKorean{abs(hash((str(path), index))) % 1_000_000}"
            try:
                pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
                _verify_korean_glyphs(name)
                return name, True
            except TTFError as exc:
                last_error = exc
                if "bad subfontIndex" in str(exc):
                    break
            except ReportPdfError:
                raise
    raise ReportPdfError(
        "korean_font_registration_failed",
        f"Korean font could not be registered: {font_path} ({last_error})",
    )


def register_report_font() -> tuple[str, bool]:
    detection = detect_korean_font()
    if detection.path:
        return _register_ttf_font(Path(detection.path))
    if strict_font_required():
        raise ReportPdfError("korean_font_not_installed", "No Korean TTF/TTC/OTF font found")
    pdfmetrics.registerFont(UnicodeCIDFont(CID_FALLBACK_FONT))
    return CID_FALLBACK_FONT, False


def _style(
    name: str,
    *,
    font_name: str,
    size: int,
    leading: int,
) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font_name,
        fontSize=size,
        leading=leading,
        spaceAfter=4 * mm,
        wordWrap="CJK",
        allowWidows=0,
        allowOrphans=0,
        borderPadding=0,
        bulletFontName=font_name,
        bulletFontSize=size,
        alignment=0,
    )


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    return Paragraph(escaped, style)


def build_report_pdf(source: ReportPdfSource) -> bytes:
    strings = [
        source.title,
        source.problem_id,
        source.problem_title,
        source.problem_domain,
        source.target_users,
        source.problem_summary,
        source.report_period,
        source.period_summary,
        *[item for pair in source.sections for item in pair],
        *source.findings,
        *source.recommendations,
        *source.evidence_ids,
    ]
    assert_clean_unicode_strings(strings)
    font_name, embedded = register_report_font()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=source.title,
        author="ZeroFounder",
    )
    title_style = _style("TitleKo", font_name=font_name, size=16, leading=22)
    heading_style = _style("HeadingKo", font_name=font_name, size=11, leading=16)
    body_style = _style("BodyKo", font_name=font_name, size=9, leading=14)
    small_style = _style("SmallKo", font_name=font_name, size=7, leading=10)
    story: list[Any] = [
        _paragraph(source.title, title_style),
        Spacer(1, 4 * mm),
        _paragraph(f"문제 ID: {source.problem_id}", body_style),
        _paragraph(f"문제명: {source.problem_title}", body_style),
        _paragraph(f"문제 영역: {source.problem_domain}", body_style),
        _paragraph(f"대상 사용자: {source.target_users}", body_style),
        _paragraph(f"문제 설명: {source.problem_summary}", body_style),
        _paragraph(f"보고 기간: {source.report_period}", body_style),
        _paragraph(f"보고 유형: {source.report_type}", body_style),
        Spacer(1, 4 * mm),
        _paragraph("기간 분석", heading_style),
        _paragraph(source.period_summary, body_style),
    ]
    for heading, content in source.sections:
        story.extend([_paragraph(heading, heading_style), _paragraph(content, body_style)])
    if source.findings:
        story.append(_paragraph("주요 발견", heading_style))
        story.extend(_paragraph(f"- {item}", body_style) for item in source.findings)
    if source.recommendations:
        story.append(_paragraph("권고 사항", heading_style))
        story.extend(_paragraph(f"- {item}", body_style) for item in source.recommendations)
    story.append(_paragraph("근거 ID", heading_style))
    story.append(_paragraph(", ".join(source.evidence_ids) or "none", body_style))
    if source.operation_key:
        story.append(Spacer(1, 5 * mm))
        story.append(_paragraph(f"operation_key: {source.operation_key}", small_style))
    if source.operation_key_hash:
        story.append(_paragraph(f"operation_key_hash: {source.operation_key_hash}", small_style))
    doc.build(story)
    pdf = buffer.getvalue()
    if embedded and b"/FontFile" not in pdf:
        raise ReportPdfError(
            "korean_font_registration_failed",
            "registered Korean font was not embedded",
        )
    return pdf


def extract_pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _font_names(reader: PdfReader) -> tuple[str, ...]:
    names: set[str] = set()
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        fonts = resources.get("/Font") or {}
        for font_ref in fonts.values():
            font = font_ref.get_object()
            base = font.get("/BaseFont")
            if base:
                names.add(str(base).lstrip("/"))
    return tuple(sorted(names))


def _render_check(pdf_bytes: bytes) -> str:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return "not_available"
    try:
        document = pdfium.PdfDocument(pdf_bytes)
        page = document[0]
        bitmap = page.render(scale=1).to_pil()
        grayscale = bitmap.convert("L")
        extrema = grayscale.getextrema()
        if extrema == (255, 255):
            return "blank_page"
        return "passed"
    except Exception:
        return "failed"


def validate_report_pdf_bytes(
    pdf_bytes: bytes,
    *,
    required_text: list[str],
    require_embedded_font: bool | None = None,
) -> PdfValidationResult:
    if not pdf_bytes.startswith(b"%PDF-") or len(pdf_bytes) < 1000:
        return PdfValidationResult(
            status="invalid_pdf_encoding",
            rejection_code="invalid_pdf_encoding",
            rejection_reason="PDF header or size is invalid",
        )
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return PdfValidationResult(
            status="invalid_pdf_encoding",
            rejection_code="invalid_pdf_encoding",
            rejection_reason="PDF could not be parsed",
        )
    if reader.is_encrypted:
        return PdfValidationResult(
            status="invalid_pdf_encoding",
            rejection_code="invalid_pdf_encoding",
            rejection_reason="PDF is encrypted",
        )
    page_count = len(reader.pages)
    font_names = _font_names(reader)
    text = extract_pdf_text(pdf_bytes)
    mojibake = contains_mojibake(text)
    missing = tuple(item for item in required_text if item and item not in text)
    fonts_embedded = b"/FontFile" in pdf_bytes
    unicode_mapping = b"/ToUnicode" in pdf_bytes or any("HY" in name for name in font_names)
    if require_embedded_font is None:
        require_embedded_font = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    render = _render_check(pdf_bytes)
    if page_count < 1:
        code = "invalid_pdf_encoding"
    elif mojibake:
        code = "mojibake_detected"
    elif (
        not font_names
        or (set(font_names) <= {"Helvetica"})
        or (require_embedded_font and not fonts_embedded)
    ):
        code = "korean_font_not_installed"
    elif missing:
        code = "pdf_text_roundtrip_failed"
    elif render in {"blank_page", "failed"}:
        code = "pdf_render_failed"
    else:
        code = ""
    return PdfValidationResult(
        status=code if code else "valid",
        rejection_code=code,
        rejection_reason="" if not code else f"PDF validation failed: {code}",
        font_names=font_names,
        fonts_embedded=fonts_embedded,
        unicode_mapping_available=unicode_mapping,
        extracted_text_check="passed" if not missing and not mojibake else "failed",
        mojibake_detected=mojibake,
        required_text_found=not missing,
        render_check=render,
        page_count=page_count,
        missing_required_text=missing,
    )
