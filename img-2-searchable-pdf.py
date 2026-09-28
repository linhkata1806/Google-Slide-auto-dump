"""Build a searchable PDF with robust, layout-aware PDF bookmarks.

All text recognition and title detection run locally with Tesseract. Targeted
OCR passes complement the full-page searchable layer. An optional smart dark
mode is limited to slides detected as dark neutral text on a light background.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence


def ensure(pip_name: str, import_name: str | None = None):
    name = import_name or pip_name
    try:
        return __import__(name)
    except ImportError:
        print(f"{pip_name} not installed. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
        return __import__(name)


ensure("pytesseract")
ensure("Pillow", "PIL")
ensure("PyMuPDF", "fitz")

import fitz  # PyMuPDF
from PIL import Image, ImageChops, ImageOps
import pytesseract
from pytesseract import Output

from slide_title_detection import (
    OCRLine,
    TitleDecision,
    detect_local_title,
    discover_repeated_templates,
    lines_from_tesseract,
    needs_rescue_ocr,
    normalize_whitespace,
    truncate_title,
)


BASE_DIR = Path(__file__).resolve().parent
CAPTURES_DIR = BASE_DIR / "captures"
OUTPUT_PDF = BASE_DIR / "result-searchable.pdf"
TEMP_OUTPUT_PDF = BASE_DIR / "result-searchable.tmp.pdf"
DEFAULT_TITLES_FILE = BASE_DIR / "titles.json"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


@dataclass
class PageAnalysis:
    index: int
    filename: str
    path: Path
    full_lines: list[OCRLine]
    lines: list[OCRLine]
    local_decision: TitleDecision | None = None


@dataclass(frozen=True)
class DarkModeAssessment:
    eligible: bool
    light_background_ratio: float
    dark_ink_ratio: float
    reason: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a searchable PDF with layout-aware bookmark titles."
    )
    parser.add_argument(
        "--titles-file",
        type=Path,
        help="UTF-8 JSON mapping 1-based page numbers to exact bookmark titles.",
    )
    parser.add_argument(
        "--debug-titles",
        action="store_true",
        help="Print ranked local title candidates for every slide.",
    )
    parser.add_argument(
        "--dark-mode",
        action="store_true",
        help=(
            "Convert only detected black-on-white slides to a dark, high-contrast "
            "render while preserving source image pixels and colored content."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--captures-dir",
        type=Path,
        help="Use existing Page_N images from this folder instead of ./captures.",
    )
    source.add_argument(
        "--input-pdf",
        type=Path,
        help="OCR and retitle an existing PDF without requiring slide captures.",
    )
    parser.add_argument(
        "--render-dpi",
        type=int,
        default=200,
        help=(
            "Analysis DPI for vector PDF pages without an extractable image; "
            "source page dimensions stay unchanged (96-600)."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination PDF (default: result-searchable.pdf next to the script).",
    )
    return parser


def find_tesseract() -> str | None:
    executable = shutil.which("tesseract")
    if executable:
        return executable
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def choose_ocr_language() -> str:
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception:
        available = set()
    if {"fra", "eng"} <= available:
        default = "fra+eng"
    elif "fra" in available:
        default = "fra"
    else:
        default = "eng"
    return os.environ.get("OCR_LANG", default)


def page_number(filename: str) -> int:
    match = re.match(r"^Page_(\d+)", filename, re.IGNORECASE)
    return int(match.group(1)) if match else 10**9


def collect_images(directory: Path | None = None) -> list[str]:
    captures_directory = directory or CAPTURES_DIR
    try:
        all_files = os.listdir(captures_directory)
    except FileNotFoundError as error:
        raise RuntimeError(f"Folder not found: {captures_directory}") from error
    files = sorted(
        [
            filename
            for filename in all_files
            if filename.lower().startswith("page_")
            and filename.lower().endswith(IMAGE_EXTENSIONS)
        ],
        key=page_number,
    )
    if not files:
        raise RuntimeError("No 'Page_X' images found in the captures folder.")
    return files


def resolve_input_pdf(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"Input PDF not found: {resolved}")
    if resolved.suffix.casefold() != ".pdf":
        raise RuntimeError(f"Input file is not a PDF: {resolved}")
    try:
        with fitz.open(resolved) as document:
            if document.needs_pass:
                raise RuntimeError("Password-protected PDFs are not supported.")
            if len(document) < 1:
                raise RuntimeError("Input PDF contains no pages.")
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"Cannot open input PDF {resolved}: {error}") from error
    return resolved


def _extract_full_page_image(
    document: fitz.Document,
    page: fitz.Page,
    directory: Path,
    index: int,
) -> str | None:
    """Extract a simple full-page image without resampling its pixels."""

    page_area = max(1.0, page.rect.width * page.rect.height)
    candidates: list[tuple[float, int]] = []
    for info in page.get_image_info(xrefs=True):
        xref = int(info.get("xref", 0))
        bbox = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
        transform = info.get("transform", (0, 0, 0, 0, 0, 0))
        if xref <= 0 or len(transform) != 6:
            continue
        a, b, c, d, _e, _f = (float(value) for value in transform)
        simple_orientation = (
            abs(b) < 0.01 and abs(c) < 0.01 and a > 0 and d > 0
        )
        coverage = max(0.0, bbox.width * bbox.height) / page_area
        if simple_orientation and coverage >= 0.90:
            candidates.append((coverage, xref))
    if not candidates:
        return None

    _coverage, xref = max(candidates)
    extracted = document.extract_image(xref)
    extension = str(extracted.get("ext", "")).casefold()
    if extension == "jpeg":
        extension = "jpg"
    suffix = f".{extension}"
    payload = extracted.get("image")
    if suffix not in IMAGE_EXTENSIONS or not isinstance(payload, bytes):
        return None
    filename = f"Page_{index}{suffix}"
    (directory / filename).write_bytes(payload)
    return filename


def render_pdf_pages(pdf_path: Path, directory: Path, dpi: int) -> list[str]:
    """Prepare page images for vision without changing embedded image resolution."""

    directory.mkdir(parents=True, exist_ok=True)
    filenames: list[str] = []
    try:
        with fitz.open(pdf_path) as document:
            page_count = len(document)
            for index, page in enumerate(document, start=1):
                filename = _extract_full_page_image(
                    document, page, directory, index
                )
                if filename:
                    method = "embedded image at original resolution"
                else:
                    filename = f"Page_{index}.png"
                    pixmap = page.get_pixmap(
                        dpi=dpi, colorspace=fitz.csRGB, alpha=False
                    )
                    pixmap.save(directory / filename)
                    method = f"temporary analysis render at {dpi} DPI"
                filenames.append(filename)
                print(f"Prepare PDF page {index}/{page_count}: {method}")
    except Exception as error:
        raise RuntimeError(f"Could not prepare input PDF pages: {error}") from error
    return filenames


def page_has_searchable_text(page: fitz.Page) -> bool:
    text = normalize_whitespace(page.get_text("text"))
    alphanumeric = sum(character.isalnum() for character in text)
    return alphanumeric >= 20 or len(text.split()) >= 4


def lines_from_pdf_text(page: fitz.Page) -> list[OCRLine]:
    """Normalize native/searchable PDF text lines for local title ranking."""

    page_rect = page.rect
    page_width = max(1.0, page_rect.width)
    page_height = max(1.0, page_rect.height)
    lines: list[OCRLine] = []
    payload = page.get_text("dict")
    for block_index, block in enumerate(payload.get("blocks", []), start=1):
        if block.get("type", 0) != 0:
            continue
        for line_index, raw_line in enumerate(block.get("lines", []), start=1):
            spans = raw_line.get("spans", [])
            text = normalize_whitespace(
                "".join(str(span.get("text", "")) for span in spans)
            )
            if not text:
                continue
            bbox = fitz.Rect(raw_line.get("bbox", (0, 0, 0, 0)))
            left = max(0.0, min(1.0, (bbox.x0 - page_rect.x0) / page_width))
            top = max(0.0, min(1.0, (bbox.y0 - page_rect.y0) / page_height))
            right = max(left, min(1.0, (bbox.x1 - page_rect.x0) / page_width))
            bottom = max(top, min(1.0, (bbox.y1 - page_rect.y0) / page_height))
            lines.append(
                OCRLine(
                    text=text,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    height=max(0.0, bottom - top),
                    confidence=99.0,
                    block_num=block_index,
                    par_num=block_index,
                    line_num=line_index,
                    source="pdf-text",
                )
            )
    return lines


def overlay_ocr_text_layer(target_page: fitz.Page, ocr_pdf: bytes) -> None:
    """Overlay only Tesseract's hidden text, preserving the source PDF visuals."""

    with fitz.open("pdf", ocr_pdf) as layer_document:
        layer_page = layer_document[0]
        image_xrefs = {image[0] for image in layer_page.get_images(full=True)}
        for xref in image_xrefs:
            layer_page.delete_image(xref)
        target_page.show_pdf_page(
            target_page.rect,
            layer_document,
            0,
            overlay=True,
            keep_proportion=False,
        )


def parse_tsv(tsv: bytes | str) -> dict[str, list[object]]:
    """Parse Tesseract TSV into the shape returned by image_to_data(DICT)."""

    columns = (
        "text",
        "conf",
        "block_num",
        "par_num",
        "line_num",
        "left",
        "top",
        "width",
        "height",
    )
    if isinstance(tsv, bytes):
        tsv = tsv.decode("utf-8", errors="replace")
    rows = tsv.splitlines()
    if not rows:
        return {column: [] for column in columns}
    header = rows[0].split("\t")
    indexes = {name: index for index, name in enumerate(header)}
    result: dict[str, list[object]] = {column: [] for column in columns}
    if "text" not in indexes:
        return result
    for row in rows[1:]:
        parts = row.split("\t")
        if len(parts) <= indexes["text"]:
            continue
        result["text"].append(parts[indexes["text"]])
        result["conf"].append(parts[indexes.get("conf", 0)])
        for column in columns[2:]:
            try:
                result[column].append(int(parts[indexes[column]]))
            except (KeyError, IndexError, ValueError):
                result[column].append(0)
    return result


def ocr_page(image: Image.Image, language: str) -> tuple[bytes, dict[str, list[object]]]:
    """Produce the searchable PDF page and full-page word geometry."""

    try:
        pdf_bytes, tsv = pytesseract.run_and_get_multiple_output(
            image, extensions=["pdf", "tsv"], lang=language
        )
        return pdf_bytes, parse_tsv(tsv)
    except Exception:
        pdf_bytes = pytesseract.image_to_pdf_or_hocr(
            image, extension="pdf", lang=language
        )
        data = pytesseract.image_to_data(
            image, lang=language, output_type=Output.DICT
        )
        return pdf_bytes, data


def ocr_region(
    image: Image.Image,
    box: tuple[int, int, int, int],
    source: str,
    language: str,
) -> list[OCRLine]:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return []
    with image.crop(box) as crop:
        data = pytesseract.image_to_data(
            crop,
            lang=language,
            config="--psm 11",
            output_type=Output.DICT,
        )
    return lines_from_tesseract(
        data,
        image.width,
        image.height,
        source=source,
        offset_x=left,
        offset_y=top,
    )


def analyze_image(
    image: Image.Image,
    full_data: dict[str, list[object]],
    language: str,
) -> tuple[list[OCRLine], list[OCRLine]]:
    full_lines = lines_from_tesseract(
        full_data, image.width, image.height, source="full"
    )
    top_left_box = (0, 0, int(image.width * 0.72), int(image.height * 0.42))
    all_lines = full_lines + ocr_region(image, top_left_box, "upper-left", language)

    if needs_rescue_ocr(all_lines):
        header_box = (0, 0, image.width, int(image.height * 0.42))
        center_box = (
            int(image.width * 0.15),
            int(image.height * 0.15),
            int(image.width * 0.85),
            int(image.height * 0.85),
        )
        all_lines.extend(ocr_region(image, header_box, "header", language))
        all_lines.extend(ocr_region(image, center_box, "center", language))
    return full_lines, all_lines


def analyze_existing_text_page(
    image: Image.Image,
    native_lines: Sequence[OCRLine],
    language: str,
) -> tuple[list[OCRLine], list[OCRLine]]:
    """Add targeted title scans without rebuilding an existing PDF text layer."""

    full_lines = list(native_lines)
    top_left_box = (0, 0, int(image.width * 0.72), int(image.height * 0.42))
    all_lines = full_lines + ocr_region(
        image, top_left_box, "upper-left", language
    )
    if needs_rescue_ocr(all_lines):
        header_box = (0, 0, image.width, int(image.height * 0.42))
        center_box = (
            int(image.width * 0.15),
            int(image.height * 0.15),
            int(image.width * 0.85),
            int(image.height * 0.85),
        )
        all_lines.extend(ocr_region(image, header_box, "header", language))
        all_lines.extend(ocr_region(image, center_box, "center", language))
    return full_lines, all_lines


def assess_dark_mode(image: Image.Image) -> DarkModeAssessment:
    """Detect slides whose dominant design is dark neutral ink on white.

    The sample is deliberately small and deterministic. Requiring a light
    neutral canvas, enough dark neutral ink, and low photographic complexity
    prevents automatic inversion of dark themes and image-heavy pages.
    """

    sample = ImageOps.contain(image.convert("RGB"), (320, 320))
    width, height = sample.size
    total = max(1, width * height)
    border_x = max(1, round(width * 0.08))
    border_y = max(1, round(height * 0.08))
    light_neutral = 0
    dark_neutral = 0
    saturated_pixels = 0
    quantized_colors: set[tuple[int, int, int]] = set()
    border_light = 0
    border_total = 0
    for index, (red, green, blue) in enumerate(sample.getdata()):
        x = index % width
        y = index // width
        luminance = (299 * red + 587 * green + 114 * blue) // 1000
        chroma = max(red, green, blue) - min(red, green, blue)
        quantized_colors.add((red // 16, green // 16, blue // 16))
        if chroma > 55:
            saturated_pixels += 1
        neutral = chroma <= 55
        is_light_background = neutral and luminance >= 220
        if is_light_background:
            light_neutral += 1
        if luminance <= 105:
            if neutral:
                dark_neutral += 1
        if x < border_x or x >= width - border_x or y < border_y or y >= height - border_y:
            border_total += 1
            if is_light_background:
                border_light += 1

    light_ratio = light_neutral / total
    dark_ratio = dark_neutral / total
    border_light_ratio = border_light / max(1, border_total)
    saturated_ratio = saturated_pixels / total
    enough_ink = dark_neutral >= max(8, round(total * 0.0015))
    light_canvas = light_ratio >= 0.50 and border_light_ratio >= 0.62
    photo_like = len(quantized_colors) >= 180 and saturated_ratio >= 0.08
    eligible = enough_ink and light_canvas and not photo_like

    if not enough_ink:
        reason = "not enough dark neutral text"
    elif not light_canvas:
        reason = "background is not predominantly light neutral"
    elif photo_like:
        reason = "page contains a complex photographic region"
    else:
        reason = "dark neutral text on a light neutral background"
    return DarkModeAssessment(
        eligible=eligible,
        light_background_ratio=light_ratio,
        dark_ink_ratio=dark_ratio,
        reason=reason,
    )


def apply_smart_dark_mode(image: Image.Image) -> Image.Image:
    """Invert neutral tones while leaving saturated colors unchanged."""

    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(maximum, minimum)
    neutral_mask = chroma.point(lambda value: 255 if value <= 55 else 0)
    return Image.composite(ImageOps.invert(rgb), rgb, neutral_mask)


def _new_image_page_with_ocr(
    image: Image.Image,
    ocr_pdf: bytes,
    page_size: tuple[float, float] | None = None,
) -> fitz.Document:
    """Create one PDF page without resampling the image pixel dimensions."""

    with fitz.open("pdf", ocr_pdf) as layer_document:
        layer_rect = layer_document[0].rect
    width, height = page_size or (layer_rect.width, layer_rect.height)
    page_document = fitz.open()
    page = page_document.new_page(width=width, height=height)
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=False)
    page.insert_image(page.rect, stream=stream.getvalue(), keep_proportion=False)
    overlay_ocr_text_layer(page, ocr_pdf)
    return page_document


def replace_page_with_image_ocr(
    document: fitz.Document,
    page_index: int,
    image: Image.Image,
    ocr_pdf: bytes,
    page_size: tuple[float, float],
) -> None:
    replacement = _new_image_page_with_ocr(image, ocr_pdf, page_size)
    try:
        document.delete_page(page_index)
        document.insert_pdf(replacement, start_at=page_index)
    finally:
        replacement.close()


def resolve_titles_path(explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return explicit_path.resolve()
    return DEFAULT_TITLES_FILE if DEFAULT_TITLES_FILE.exists() else None


def load_title_overrides(path: Path | None, page_count: int) -> dict[int, str]:
    if path is None:
        return {}
    if not path.is_file():
        raise ValueError(f"Titles file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid titles file {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("Titles file must contain a JSON object: {page: title}.")

    result: dict[int, str] = {}
    for raw_page, raw_title in payload.items():
        try:
            page = int(raw_page)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid page number in titles file: {raw_page!r}") from error
        if isinstance(raw_title, bool) or not isinstance(raw_title, str):
            raise ValueError(f"Title for page {page} must be a string.")
        title = truncate_title(raw_title)
        if not title:
            raise ValueError(f"Title for page {page} cannot be empty.")
        if page < 1 or page > page_count:
            print(f"WARNING: ignoring title override for out-of-range page {page}.")
            continue
        result[page] = title
    print(f"Manual title overrides: {len(result)} loaded from {path}")
    return result


def print_decision(page: PageAnalysis, decision: TitleDecision, debug: bool) -> None:
    print(
        f"Title {page.index}: {decision.title!r} "
        f"[{decision.strategy}, confidence={decision.confidence:.2f}, source={decision.source}]"
    )
    if debug and page.local_decision:
        for candidate in page.local_decision.diagnostics:
            repeated = " template" if candidate.repeated_template else ""
            print(
                f"    {candidate.strategy:<10} {candidate.score:>5.2f} "
                f"{candidate.source:<10}{repeated}: {candidate.text}"
            )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_path = args.output.expanduser().resolve() if args.output else OUTPUT_PDF
    temporary_output_path = (
        output_path.with_name(
            f".{output_path.stem}.{os.getpid()}.tmp{output_path.suffix}"
        )
        if args.output
        else TEMP_OUTPUT_PDF
    )
    if not 96 <= args.render_dpi <= 600:
        print("ERROR: --render-dpi must be between 96 and 600.")
        return 2

    print("\n=== Searchable PDF + robust table of contents (OCR) ===")
    print(f"Smart dark mode: {'enabled' if args.dark_mode else 'disabled'}")

    temporary_source: tempfile.TemporaryDirectory[str] | None = None
    input_pdf_path: Path | None = None
    try:
        if args.input_pdf:
            input_pdf_path = resolve_input_pdf(args.input_pdf)
            if input_pdf_path == output_path.resolve():
                raise RuntimeError(
                    "Input and output PDF paths are identical. Use --output with "
                    "a different filename so the source PDF remains untouched."
                )
            temporary_source = tempfile.TemporaryDirectory(
                prefix="googledump-pdf-pages-"
            )
            source_directory = Path(temporary_source.name)
            print(f"Input PDF  : {input_pdf_path}")
            files = render_pdf_pages(
                input_pdf_path, source_directory, args.render_dpi
            )
        else:
            source_directory = (
                args.captures_dir.expanduser().resolve()
                if args.captures_dir
                else CAPTURES_DIR
            )
            files = collect_images(source_directory)
            print(f"Captures   : {source_directory}")
        overrides = load_title_overrides(
            resolve_titles_path(args.titles_file), len(files)
        )
    except (RuntimeError, ValueError) as error:
        if temporary_source:
            temporary_source.cleanup()
        print(f"ERROR: {error}")
        return 2

    merged = fitz.open()
    source_searchable_pages: list[bool] = []
    source_native_lines: list[list[OCRLine]] = []
    source_page_sizes: list[tuple[float, float]] = []
    source_text_pages_detected = 0
    if input_pdf_path:
        try:
            with fitz.open(input_pdf_path) as source_document:
                detected_text_pages = [
                    page_has_searchable_text(source_document[page_index])
                    for page_index in range(len(source_document))
                ]
                source_native_lines = [
                    lines_from_pdf_text(source_document[page_index])
                    for page_index in range(len(source_document))
                ]
                source_page_sizes = [
                    (
                        float(source_document[page_index].rect.width),
                        float(source_document[page_index].rect.height),
                    )
                    for page_index in range(len(source_document))
                ]
                source_text_pages_detected = sum(detected_text_pages)
                document_is_already_ocr = source_text_pages_detected >= max(
                    1, math.ceil(len(source_document) * 0.80)
                )
                source_searchable_pages = (
                    [True] * len(source_document)
                    if document_is_already_ocr
                    else detected_text_pages
                )
                merged.insert_pdf(source_document)
                metadata = {
                    key: value
                    for key, value in source_document.metadata.items()
                    if isinstance(value, str)
                }
                if metadata:
                    merged.set_metadata(metadata)
                if document_is_already_ocr:
                    print(
                        "Existing PDF text layer: "
                        f"{source_text_pages_detected}/{len(source_document)} "
                        "pages; full-page re-OCR disabled."
                    )
        except Exception as error:
            if temporary_source:
                temporary_source.cleanup()
            merged.close()
            print(f"ERROR: cannot preserve input PDF pages: {error}")
            return 2

    requires_ocr_layers = (
        args.dark_mode
        or not input_pdf_path
        or not all(source_searchable_pages)
    )
    tesseract_executable = find_tesseract()
    if tesseract_executable:
        pytesseract.pytesseract.tesseract_cmd = tesseract_executable
        language = choose_ocr_language()
        print(f"Tesseract : {tesseract_executable}")
        print(f"Language  : {language}")
        if input_pdf_path and all(source_searchable_pages) and not args.dark_mode:
            print("OCR mode  : targeted title scans only; existing PDF text is preserved")
    else:
        language = ""
        if requires_ocr_layers:
            if temporary_source:
                temporary_source.cleanup()
            merged.close()
            print("ERROR: Tesseract OCR engine not found on this machine.")
            print("Install it (Windows): winget install UB-Mannheim.TesseractOCR")
            return 1
        print(
            "WARNING: Tesseract is unavailable; using the existing PDF text "
            "for titles without targeted scans."
        )

    analyses: list[PageAnalysis] = []
    output_completed = False
    ocr_layers_added = 0
    dark_pages_applied = 0
    try:
        for index, filename in enumerate(files, start=1):
            path = source_directory / filename
            existing_text = bool(
                input_pdf_path and source_searchable_pages[index - 1]
            )
            dark_assessment: DarkModeAssessment | None = None
            original_image: Image.Image | None = None
            if args.dark_mode:
                with Image.open(path) as image:
                    image.load()
                    original_image = image.convert("RGB")
                dark_assessment = assess_dark_mode(original_image)

            requires_ocr = not existing_text or bool(
                dark_assessment and dark_assessment.eligible
            )
            if not requires_ocr:
                print(
                    f"PDF text {index}/{len(files)} : {filename} "
                    "(existing OCR reused)"
                )
                if tesseract_executable:
                    if original_image is None:
                        with Image.open(path) as image:
                            image.load()
                            original_image = image.convert("RGB")
                    full_lines, all_lines = analyze_existing_text_page(
                        original_image,
                        source_native_lines[index - 1],
                        language,
                    )
                else:
                    full_lines = source_native_lines[index - 1]
                    all_lines = list(full_lines)
            else:
                print(f"OCR {index}/{len(files)} : {filename}")
                if original_image is None:
                    with Image.open(path) as image:
                        image.load()
                        original_image = image.convert("RGB")
                pdf_bytes, full_data = ocr_page(original_image, language)
                full_lines, all_lines = analyze_image(
                    original_image, full_data, language
                )
                if dark_assessment and dark_assessment.eligible:
                    dark_image = apply_smart_dark_mode(original_image)
                    try:
                        if input_pdf_path:
                            replace_page_with_image_ocr(
                                merged,
                                index - 1,
                                dark_image,
                                pdf_bytes,
                                source_page_sizes[index - 1],
                            )
                            ocr_layers_added += 1
                        else:
                            with _new_image_page_with_ocr(
                                dark_image, pdf_bytes
                            ) as page_pdf:
                                merged.insert_pdf(page_pdf)
                        dark_pages_applied += 1
                    finally:
                        dark_image.close()
                    print(
                        f"  dark mode applied "
                        f"(light background={dark_assessment.light_background_ratio:.0%}, "
                        f"dark neutral ink={dark_assessment.dark_ink_ratio:.1%})"
                    )
                elif input_pdf_path:
                    overlay_ocr_text_layer(merged[index - 1], pdf_bytes)
                    ocr_layers_added += 1
                else:
                    with fitz.open("pdf", pdf_bytes) as page_pdf:
                        merged.insert_pdf(page_pdf)

            if args.dark_mode and dark_assessment and not dark_assessment.eligible:
                if args.debug_titles:
                    print(f"  dark mode skipped: {dark_assessment.reason}")
            if original_image is not None:
                original_image.close()
            analyses.append(
                PageAnalysis(
                    index=index,
                    filename=filename,
                    path=path,
                    full_lines=full_lines,
                    lines=all_lines,
                )
            )

        repeated_templates = discover_repeated_templates(
            [analysis.lines for analysis in analyses]
        )
        for analysis in analyses:
            analysis.local_decision = detect_local_title(
                analysis.lines, repeated_templates, analysis.index
            )

        toc: list[list[object]] = []
        final_decisions: dict[int, TitleDecision] = {}
        for analysis in analyses:
            if analysis.index in overrides:
                decision = TitleDecision(
                    title=overrides[analysis.index],
                    strategy="manual",
                    confidence=1.0,
                    source=str(resolve_titles_path(args.titles_file)),
                )
            else:
                if analysis.local_decision is None:
                    raise RuntimeError(
                        f"Local title detection did not return page {analysis.index}."
                    )
                decision = analysis.local_decision
            final_decisions[analysis.index] = decision
            toc.append([1, decision.title, analysis.index])
            print_decision(analysis, decision, args.debug_titles)

        merged.set_toc(toc)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if temporary_output_path.exists():
            temporary_output_path.unlink()
        merged.save(temporary_output_path, deflate=True, garbage=4)
        merged.close()
        os.replace(temporary_output_path, output_path)
        output_completed = True
    except Exception as error:
        print(f"ERROR: searchable PDF generation failed: {error}")
        return 1
    finally:
        if not merged.is_closed:
            merged.close()
        if not output_completed and temporary_output_path.exists():
            try:
                temporary_output_path.unlink()
            except OSError:
                pass
        if temporary_source:
            temporary_source.cleanup()

    print("\n=== Done ===")
    print(f"Searchable PDF : {output_path}")
    print(f"Pages          : {len(analyses)}")
    if input_pdf_path:
        print(
            f"OCR layers     : {ocr_layers_added} added, "
            f"{len(analyses) - ocr_layers_added} already searchable"
        )
    if args.dark_mode:
        print(f"Dark-mode pages: {dark_pages_applied}/{len(analyses)}")
    print("Table of contents:")
    for analysis in analyses:
        decision = final_decisions[analysis.index]
        print(f"  p.{analysis.index:>3}  {decision.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
