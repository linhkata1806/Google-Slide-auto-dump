"""Deterministic, layout-aware title detection for presentation pages.

The module is deliberately independent from Tesseract, Pillow, and PyMuPDF.
It consumes normalized OCR lines and combines geometry, typography, document-
wide repetition, and conservative fallbacks. This keeps the ranking fast,
testable, and fully local.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import math
import re
import statistics
from typing import Iterable, Mapping, Sequence


MAX_TITLE_LENGTH = 120
MIN_OCR_CONFIDENCE = 40.0
TEMPLATE_PAGE_RATIO = 0.30


@dataclass(frozen=True)
class OCRLine:
    """One OCR line using coordinates normalized to the complete slide."""

    text: str
    left: float
    top: float
    right: float
    bottom: float
    height: float
    confidence: float
    block_num: int = 0
    par_num: int = 0
    line_num: int = 0
    source: str = "full"

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def paragraph_key(self) -> tuple[int, int]:
        return self.block_num, self.par_num


@dataclass(frozen=True)
class CandidateDiagnostic:
    text: str
    strategy: str
    score: float
    source: str
    repeated_template: bool = False


@dataclass(frozen=True)
class TitleDecision:
    title: str
    strategy: str
    confidence: float
    source: str
    diagnostics: tuple[CandidateDiagnostic, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _Candidate:
    lines: tuple[OCRLine, ...]
    text: str
    left: float
    top: float
    right: float
    bottom: float
    height: float
    confidence: float
    source: str

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def width(self) -> float:
        return self.right - self.left


TemplateKey = tuple[str, int, int]


_PAGE_NUMBER_RE = re.compile(
    r"^(?:(?:slide|page|diapo(?:sitive)?)\s*)?\d+(?:\s*[/|]\s*\d+)?$",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"^(?:(?:0?[1-9]|[12]\d|3[01])\s*[/.-]\s*(?:0?[1-9]|1[0-2])"
    r"(?:\s*[/.-]\s*(?:19|20)?\d{2})?|(?:19|20)\d{2})$"
)
_URL_OR_EMAIL_RE = re.compile(
    r"(?:https?://|www\.|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b)", re.IGNORECASE
)
_BULLET_PREFIXES = ("•", "◦", "▪", "▫", "‣", "⁃", "- ", "– ", "— ", "* ")
_SOURCE_PRIORITY = {
    "upper-left": 0,
    "header": 1,
    "center": 2,
    "pdf-text": 3,
    "full": 4,
}


def normalize_whitespace(text: str) -> str:
    """Collapse OCR whitespace while retaining visible decoration and casing."""

    return re.sub(r"\s+", " ", text or "").strip()


def truncate_title(text: str, limit: int = MAX_TITLE_LENGTH) -> str:
    text = normalize_whitespace(text)
    if len(text) <= limit:
        return text
    shortened = text[: limit + 1]
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip()


def canonical_text(text: str) -> str:
    """Return a comparison form while leaving the display text untouched."""

    return " ".join(
        "".join(char.casefold() for char in token if char.isalnum())
        for token in normalize_whitespace(text).split()
        if any(char.isalnum() for char in token)
    )


def is_meaningful_text(text: str) -> bool:
    cleaned = normalize_whitespace(text)
    alphanumeric = "".join(char for char in cleaned if char.isalnum())
    if len(alphanumeric) >= 3:
        return True
    return len(alphanumeric) == 2 and alphanumeric.isupper()


def is_page_number(text: str) -> bool:
    return bool(_PAGE_NUMBER_RE.fullmatch(normalize_whitespace(text)))


def lines_from_tesseract(
    data: Mapping[str, Sequence[object]],
    page_width: int,
    page_height: int,
    *,
    source: str = "full",
    offset_x: int = 0,
    offset_y: int = 0,
    minimum_confidence: float = MIN_OCR_CONFIDENCE,
) -> list[OCRLine]:
    """Group Tesseract words into normalized lines, including crop offsets."""

    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive")

    grouped: dict[tuple[int, int, int], dict[str, list[object]]] = {}
    texts = data.get("text", ())
    for index in range(len(texts)):
        text = normalize_whitespace(str(texts[index] or ""))
        try:
            confidence = float(data.get("conf", ())[index])
        except (IndexError, TypeError, ValueError):
            confidence = -1.0
        if not text or confidence < minimum_confidence:
            continue

        def integer(field: str) -> int:
            try:
                return int(data.get(field, ())[index])
            except (IndexError, TypeError, ValueError):
                return 0

        block_num = integer("block_num")
        par_num = integer("par_num")
        line_num = integer("line_num")
        key = block_num, par_num, line_num
        entry = grouped.setdefault(
            key,
            {
                "words": [],
                "lefts": [],
                "tops": [],
                "rights": [],
                "bottoms": [],
                "heights": [],
                "confidences": [],
            },
        )
        left = integer("left") + offset_x
        top = integer("top") + offset_y
        width = max(0, integer("width"))
        height = max(0, integer("height"))
        entry["words"].append((left, text))
        entry["lefts"].append(left)
        entry["tops"].append(top)
        entry["rights"].append(left + width)
        entry["bottoms"].append(top + height)
        entry["heights"].append(height)
        entry["confidences"].append(confidence)

    result: list[OCRLine] = []
    for (block_num, par_num, line_num), entry in grouped.items():
        words = " ".join(text for _left, text in sorted(entry["words"]))
        left = min(entry["lefts"]) / page_width
        top = min(entry["tops"]) / page_height
        right = max(entry["rights"]) / page_width
        bottom = max(entry["bottoms"]) / page_height
        median_height = statistics.median(entry["heights"]) / page_height
        result.append(
            OCRLine(
                text=normalize_whitespace(words),
                left=max(0.0, min(1.0, left)),
                top=max(0.0, min(1.0, top)),
                right=max(0.0, min(1.0, right)),
                bottom=max(0.0, min(1.0, bottom)),
                height=max(0.0, min(1.0, median_height)),
                confidence=sum(entry["confidences"]) / len(entry["confidences"]),
                block_num=block_num,
                par_num=par_num,
                line_num=line_num,
                source=source,
            )
        )
    return sorted(result, key=lambda line: (line.top, line.left))


def _usable_line(line: OCRLine) -> bool:
    return (
        line.confidence >= MIN_OCR_CONFIDENCE
        and is_meaningful_text(line.text)
        and not is_page_number(line.text)
        and line.bottom >= 0.0
        and line.top <= 1.0
    )


def _position_bucket(line: OCRLine) -> tuple[int, int]:
    return round(line.center_x / 0.04), round(line.center_y / 0.04)


def _template_key(line: OCRLine) -> TemplateKey:
    x_bucket, y_bucket = _position_bucket(line)
    return canonical_text(line.text), x_bucket, y_bucket


def _same_template_text(first: str, second: str, threshold: float) -> bool:
    if first == second:
        return True
    # Numbered chapters and titles are semantically distinct even when the rest
    # of the wording is identical. Fuzzy matching is reserved for OCR jitter.
    if re.findall(r"\d+", first) != re.findall(r"\d+", second):
        return False
    if min(len(first), len(second)) < 6:
        return False
    return SequenceMatcher(None, first, second).ratio() >= threshold


def discover_repeated_templates(pages: Sequence[Sequence[OCRLine]]) -> set[TemplateKey]:
    """Find fuzzy text repeated near the same position on at least 30% of pages."""

    if len(pages) < 3:
        return set()
    clusters: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for page_index, raw_lines in enumerate(pages):
        for line in _deduplicate_lines(raw_lines):
            if not _usable_line(line):
                continue
            canonical = canonical_text(line.text)
            x_bucket, y_bucket = _position_bucket(line)
            matching: dict[str, object] | None = None
            for neighbor_x in range(x_bucket - 1, x_bucket + 2):
                for neighbor_y in range(y_bucket - 1, y_bucket + 2):
                    for cluster in clusters.get((neighbor_x, neighbor_y), []):
                        if _same_template_text(
                            canonical, str(cluster["text"]), 0.86
                        ):
                            matching = cluster
                            break
                    if matching:
                        break
                if matching:
                    break
            if matching is None:
                matching = {
                    "text": canonical,
                    "x": x_bucket,
                    "y": y_bucket,
                    "pages": set(),
                }
                clusters[(x_bucket, y_bucket)].append(matching)
            pages_seen = matching["pages"]
            assert isinstance(pages_seen, set)
            pages_seen.add(page_index)

    threshold = max(3, math.ceil(len(pages) * TEMPLATE_PAGE_RATIO))
    result: set[TemplateKey] = set()
    for bucket_clusters in clusters.values():
        for cluster in bucket_clusters:
            pages_seen = cluster["pages"]
            assert isinstance(pages_seen, set)
            if len(pages_seen) >= threshold:
                result.add(
                    (str(cluster["text"]), int(cluster["x"]), int(cluster["y"]))
                )
    return result


def _is_template(line: OCRLine, repeated_templates: set[TemplateKey]) -> bool:
    canonical = canonical_text(line.text)
    x_bucket, y_bucket = _position_bucket(line)
    for template_text, template_x, template_y in repeated_templates:
        if abs(x_bucket - template_x) > 1 or abs(y_bucket - template_y) > 1:
            continue
        if _same_template_text(canonical, template_text, 0.84):
            return True
    return False


def _deduplicate_lines(lines: Iterable[OCRLine]) -> list[OCRLine]:
    """Merge results from full-page and targeted OCR passes."""

    result: list[OCRLine] = []
    ordered = sorted(
        (line for line in lines if _usable_line(line)),
        key=lambda line: (
            _SOURCE_PRIORITY.get(line.source, 5),
            line.top,
            line.left,
            -line.confidence,
        ),
    )
    for candidate in ordered:
        canonical = canonical_text(candidate.text)
        duplicate_index: int | None = None
        for index, current in enumerate(result):
            same_place = (
                abs(candidate.center_x - current.center_x) <= 0.045
                and abs(candidate.center_y - current.center_y) <= 0.038
            )
            similarity = SequenceMatcher(
                None, canonical, canonical_text(current.text)
            ).ratio()
            if same_place and similarity >= 0.84:
                duplicate_index = index
                break
        if duplicate_index is None:
            result.append(candidate)
            continue
        current = result[duplicate_index]
        candidate_rank = (
            _SOURCE_PRIORITY.get(candidate.source, 5),
            -candidate.confidence,
        )
        current_rank = (
            _SOURCE_PRIORITY.get(current.source, 5),
            -current.confidence,
        )
        if candidate_rank < current_rank:
            result[duplicate_index] = candidate
    return sorted(result, key=lambda line: (line.top, line.left))


def _primary_lines(lines: Sequence[OCRLine]) -> list[OCRLine]:
    return [line for line in lines if line.source in {"full", "pdf-text"}]


def _median_body_height(
    lines: Sequence[OCRLine], repeated_templates: set[TemplateKey]
) -> float:
    body = [
        line.height
        for line in lines
        if 0.16 <= line.center_y <= 0.88
        and line.height > 0
        and not _is_template(line, repeated_templates)
    ]
    if len(body) < 3:
        body = [
            line.height
            for line in lines
            if line.height > 0 and not _is_template(line, repeated_templates)
        ]
    return statistics.median(body) if body else 0.025


def _candidate_from_lines(lines: Sequence[OCRLine]) -> _Candidate:
    ordered = tuple(sorted(lines, key=lambda line: (line.top, line.left)))
    return _Candidate(
        lines=ordered,
        text=truncate_title(" ".join(line.text for line in ordered)),
        left=min(line.left for line in ordered),
        top=min(line.top for line in ordered),
        right=max(line.right for line in ordered),
        bottom=max(line.bottom for line in ordered),
        height=statistics.median(line.height for line in ordered),
        confidence=sum(line.confidence for line in ordered) / len(ordered),
        source=ordered[0].source,
    )


def _can_merge(previous: OCRLine, following: OCRLine, seed: OCRLine) -> bool:
    if following.top <= previous.top:
        return False
    gap = following.top - previous.bottom
    height_ratio = following.height / max(previous.height, 0.001)
    left_aligned = abs(following.left - seed.left) <= 0.045
    center_aligned = abs(following.center_x - seed.center_x) <= 0.065
    close_enough = -0.012 <= gap <= max(
        0.038, 1.45 * max(previous.height, following.height)
    )
    compatible_size = 0.62 <= height_ratio <= 1.55
    # A visibly smaller subtitle should remain separate from the main title.
    if height_ratio < 0.76 and gap > 0.014:
        compatible_size = False
    return close_enough and compatible_size and (left_aligned or center_aligned)


def _build_candidates(lines: Sequence[OCRLine]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for index, seed in enumerate(lines):
        selected = [seed]
        candidates.append(_candidate_from_lines(selected))
        previous = seed
        for following in lines[index + 1 :]:
            if following.top - previous.bottom > 0.09:
                break
            if not _can_merge(previous, following, seed):
                continue
            combined = normalize_whitespace(
                " ".join(line.text for line in selected + [following])
            )
            if len(combined) > MAX_TITLE_LENGTH or len(combined.split()) > 22:
                break
            selected.append(following)
            candidates.append(_candidate_from_lines(selected))
            previous = following
            if len(selected) >= 3:
                break

    unique: dict[tuple[str, int, int], _Candidate] = {}
    for candidate in candidates:
        key = (
            canonical_text(candidate.text),
            round(candidate.left / 0.02),
            round(candidate.top / 0.02),
        )
        current = unique.get(key)
        if current is None or len(candidate.lines) > len(current.lines):
            unique[key] = candidate
    return list(unique.values())


def _candidate_is_template(
    candidate: _Candidate, repeated_templates: set[TemplateKey]
) -> bool:
    return all(_is_template(line, repeated_templates) for line in candidate.lines)


def _noise_penalty(text: str, *, paragraph_sensitive: bool = True) -> float:
    cleaned = normalize_whitespace(text)
    words = cleaned.split()
    penalty = 0.0
    if _URL_OR_EMAIL_RE.search(cleaned):
        penalty += 0.55
    if _DATE_RE.fullmatch(cleaned):
        penalty += 0.38
    if cleaned.startswith(_BULLET_PREFIXES):
        penalty += 0.34
    if len(cleaned) > 100:
        penalty += 0.16
    if len(words) > 18:
        penalty += 0.18
    if paragraph_sensitive and len(words) > 10 and cleaned.rstrip().endswith(
        (".", "!", "?", "…", ".”", "!”", "?”")
    ):
        penalty += 0.15
    alphanumeric = [char for char in cleaned if char.isalnum()]
    if alphanumeric:
        digit_share = sum(char.isdigit() for char in alphanumeric) / len(alphanumeric)
        if digit_share > 0.65:
            penalty += 0.18
    return penalty


def _size_score(height: float, body_height: float) -> float:
    ratio = height / max(body_height, 0.012)
    return max(0.0, min(1.0, (ratio - 0.55) / 1.25))


def _confidence_score(confidence: float) -> float:
    return max(0.0, min(1.0, (confidence - 35.0) / 65.0))


def _dominance_score(candidate: _Candidate, candidates: Sequence[_Candidate]) -> float:
    single_line_heights = [item.height for item in candidates if len(item.lines) == 1]
    maximum = max(single_line_heights, default=candidate.height)
    return min(1.0, candidate.height / max(maximum, 0.012))


def _score_section(
    candidate: _Candidate,
    body_height: float,
    candidates: Sequence[_Candidate],
    repeated: bool,
) -> float:
    center_x = max(0.0, 1.0 - abs(candidate.center_x - 0.5) / 0.34)
    center_y = max(0.0, 1.0 - abs(candidate.center_y - 0.5) / 0.43)
    score = (
        0.31 * _size_score(candidate.height, body_height)
        + 0.23 * center_x
        + 0.13 * center_y
        + 0.13 * _confidence_score(candidate.confidence)
        + 0.20 * _dominance_score(candidate, candidates)
        + 0.025 * (len(candidate.lines) - 1)
        - _noise_penalty(candidate.text)
    )
    if repeated:
        score -= 0.55
    return score


def _score_upper_left(
    candidate: _Candidate, body_height: float, repeated: bool
) -> float:
    vertical = max(0.0, 1.0 - candidate.top / 0.32)
    left = max(0.0, 1.0 - candidate.left / 0.40)
    score = (
        0.44 * vertical
        + 0.25 * _size_score(candidate.height, body_height)
        + 0.13 * left
        + 0.10 * _confidence_score(candidate.confidence)
        + 0.08 * min(1.0, candidate.width / 0.34)
        + 0.025 * (len(candidate.lines) - 1)
        - _noise_penalty(candidate.text)
    )
    if repeated:
        score -= 0.52
    return score


def _score_header(
    candidate: _Candidate, body_height: float, repeated: bool
) -> float:
    vertical = max(0.0, 1.0 - candidate.top / 0.31)
    horizontal = max(0.0, 1.0 - abs(candidate.center_x - 0.5) / 0.52)
    score = (
        0.42 * vertical
        + 0.28 * _size_score(candidate.height, body_height)
        + 0.16 * horizontal
        + 0.14 * _confidence_score(candidate.confidence)
        + 0.025 * (len(candidate.lines) - 1)
        - _noise_penalty(candidate.text)
    )
    if candidate.left > 0.68 and candidate.width < 0.28:
        score -= 0.25
    if repeated:
        score -= 0.52
    return score


def first_sentence(text: str) -> str:
    """Extract a display-safe first sentence, preserving closing quotes."""

    text = normalize_whitespace(text)
    match = re.search(r"[.!?…]", text)
    if match:
        end = match.end()
        closing = '"\'’”»)]}'
        while end < len(text) and text[end] in closing:
            end += 1
        if end < len(text) and text[end].isspace():
            next_character = end
            while next_character < len(text) and text[next_character].isspace():
                next_character += 1
            if next_character < len(text) and text[next_character] in closing:
                end = next_character + 1
        return truncate_title(text[:end])
    return truncate_title(text)


def _first_paragraph(
    primary_lines: Sequence[OCRLine], repeated_templates: set[TemplateKey]
) -> tuple[str, str] | None:
    paragraphs: dict[tuple[str, int, int], list[OCRLine]] = defaultdict(list)
    for line in primary_lines:
        if (
            not _usable_line(line)
            or _is_template(line, repeated_templates)
            or line.top >= 0.90
            or _URL_OR_EMAIL_RE.search(line.text)
            or _DATE_RE.fullmatch(normalize_whitespace(line.text))
        ):
            continue
        paragraphs[(line.source, *line.paragraph_key)].append(line)

    ordered = sorted(
        paragraphs.values(),
        key=lambda group: (
            min(line.top for line in group),
            min(line.left for line in group),
        ),
    )
    for group in ordered:
        visual_lines = sorted(group, key=lambda line: (line.top, line.left))
        text = normalize_whitespace(" ".join(line.text for line in visual_lines))
        if not is_meaningful_text(text) or text.startswith(_BULLET_PREFIXES):
            continue
        # Skip tiny logo-like fragments before the first real paragraph.
        if len(text.split()) <= 2 and len(canonical_text(text)) < 8:
            continue
        return first_sentence(text), visual_lines[0].source
    return None


def _diagnostics_tuple(
    diagnostics: Sequence[CandidateDiagnostic],
) -> tuple[CandidateDiagnostic, ...]:
    return tuple(sorted(diagnostics, key=lambda item: item.score, reverse=True))


def needs_rescue_ocr(lines: Sequence[OCRLine]) -> bool:
    """Return whether full-width header and center OCR crops may help."""

    decision = detect_local_title(lines, set(), page_number=1, allow_paragraph=False)
    return decision.strategy == "none" or decision.confidence < 0.65


def detect_local_title(
    lines: Sequence[OCRLine],
    repeated_templates: set[TemplateKey],
    page_number: int,
    *,
    allow_paragraph: bool = True,
) -> TitleDecision:
    """Choose a slide title using layout, typography, and deck-wide context."""

    usable = _deduplicate_lines(lines)
    primary = _primary_lines(usable)
    layout_lines = primary or usable
    body_height = _median_body_height(layout_lines, repeated_templates)
    candidates = _build_candidates(usable)
    diagnostics: list[CandidateDiagnostic] = []

    word_count = sum(len(line.text.split()) for line in layout_lines)
    sparse = len(layout_lines) <= 6 and word_count <= 40
    if sparse:
        section_candidates: list[tuple[float, _Candidate]] = []
        for candidate in candidates:
            dominant = candidate.height >= 0.038 or (
                len(layout_lines) >= 3 and candidate.height >= body_height * 1.30
            )
            if not (
                dominant
                and 0.28 <= candidate.center_x <= 0.72
                and 0.14 <= candidate.center_y <= 0.84
            ):
                continue
            repeated = _candidate_is_template(candidate, repeated_templates)
            score = _score_section(candidate, body_height, candidates, repeated)
            section_candidates.append((score, candidate))
            diagnostics.append(
                CandidateDiagnostic(
                    candidate.text, "section", score, candidate.source, repeated
                )
            )
        if section_candidates:
            score, candidate = max(section_candidates, key=lambda item: item[0])
            if score >= 0.55:
                return TitleDecision(
                    title=candidate.text,
                    strategy="section",
                    confidence=max(0.0, min(1.0, score)),
                    source=candidate.source,
                    diagnostics=_diagnostics_tuple(diagnostics),
                )

    upper_left_candidates: list[tuple[float, _Candidate]] = []
    for candidate in candidates:
        if candidate.top > 0.28 or candidate.left > 0.30:
            continue
        repeated = _candidate_is_template(candidate, repeated_templates)
        score = _score_upper_left(candidate, body_height, repeated)
        upper_left_candidates.append((score, candidate))
        diagnostics.append(
            CandidateDiagnostic(
                candidate.text, "upper-left", score, candidate.source, repeated
            )
        )
    if upper_left_candidates:
        score, candidate = max(upper_left_candidates, key=lambda item: item[0])
        if score >= 0.50:
            return TitleDecision(
                title=candidate.text,
                strategy="upper-left",
                confidence=max(0.0, min(1.0, score)),
                source=candidate.source,
                diagnostics=_diagnostics_tuple(diagnostics),
            )

    header_candidates: list[tuple[float, _Candidate]] = []
    for candidate in candidates:
        if candidate.top > 0.29:
            continue
        repeated = _candidate_is_template(candidate, repeated_templates)
        score = _score_header(candidate, body_height, repeated)
        header_candidates.append((score, candidate))
        diagnostics.append(
            CandidateDiagnostic(
                candidate.text, "header", score, candidate.source, repeated
            )
        )
    if header_candidates:
        score, candidate = max(header_candidates, key=lambda item: item[0])
        if score >= 0.54:
            return TitleDecision(
                title=candidate.text,
                strategy="header",
                confidence=max(0.0, min(1.0, score)),
                source=candidate.source,
                diagnostics=_diagnostics_tuple(diagnostics),
            )

    non_template = [
        line for line in usable if not _is_template(line, repeated_templates)
    ]
    repeated_headers = [
        line
        for line in usable
        if _is_template(line, repeated_templates)
        and (
            line.top <= 0.38
            or (
                sparse
                and 0.28 <= line.center_x <= 0.72
                and 0.14 <= line.center_y <= 0.84
            )
        )
    ]
    if repeated_headers and not non_template:
        candidate = _candidate_from_lines(
            [
                max(
                    repeated_headers,
                    key=lambda line: (line.height, line.confidence, -line.top),
                )
            ]
        )
        return TitleDecision(
            title=candidate.text,
            strategy="template-title",
            confidence=0.30,
            source=candidate.source,
            diagnostics=_diagnostics_tuple(diagnostics),
        )

    if allow_paragraph:
        paragraph = _first_paragraph(primary, repeated_templates)
        if paragraph:
            title, source = paragraph
            return TitleDecision(
                title=title,
                strategy="paragraph",
                confidence=0.35,
                source=source,
                diagnostics=_diagnostics_tuple(diagnostics),
            )
        return TitleDecision(
            title=f"Slide {page_number}",
            strategy="fallback",
            confidence=0.0,
            source="generated",
            diagnostics=_diagnostics_tuple(diagnostics),
        )

    return TitleDecision(
        title="",
        strategy="none",
        confidence=0.0,
        source="none",
        diagnostics=_diagnostics_tuple(diagnostics),
    )
