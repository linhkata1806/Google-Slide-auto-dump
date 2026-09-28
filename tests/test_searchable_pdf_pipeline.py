import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import fitz
from PIL import Image, ImageDraw

from slide_title_detection import OCRLine


def load_script_module():
    name = "searchable_pdf_script_for_tests"
    spec = importlib.util.spec_from_file_location(name, "img-2-searchable-pdf.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = load_script_module()


def title_line(text="Fixture title", source="full"):
    return OCRLine(
        text=text,
        left=0.04,
        top=0.05,
        right=0.35,
        bottom=0.09,
        height=0.04,
        confidence=96.0,
        block_num=1,
        par_num=1,
        line_num=1,
        source=source,
    )


def searchable_page_bytes():
    document = fitz.open()
    page = document.new_page(width=800, height=450)
    page.insert_text((72, 72), "Searchable fixture text")
    payload = document.tobytes()
    document.close()
    return payload


class SearchablePdfPipelineTests(unittest.TestCase):
    def test_manual_title_file_is_validated_and_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "titles.json"
            path.write_text(
                json.dumps({"2": "Quarterly overview", "99": "Ignored"}),
                encoding="utf-8",
            )
            result = SCRIPT.load_title_overrides(path, 3)
            self.assertEqual(result, {2: "Quarterly overview"})

            path.write_text('{"1": ""}', encoding="utf-8")
            with self.assertRaises(ValueError):
                SCRIPT.load_title_overrides(path, 3)

    def test_pipeline_creates_searchable_pdf_and_bookmark(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / "captures"
            captures.mkdir()
            Image.new("RGB", (800, 450), "white").save(captures / "Page_1.png")
            output = root / "result-searchable.pdf"
            temporary = root / "result-searchable.tmp.pdf"

            with (
                mock.patch.object(SCRIPT, "CAPTURES_DIR", captures),
                mock.patch.object(SCRIPT, "OUTPUT_PDF", output),
                mock.patch.object(SCRIPT, "TEMP_OUTPUT_PDF", temporary),
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "titles.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    return_value=(searchable_page_bytes(), {"text": []}),
                ),
                mock.patch.object(
                    SCRIPT, "analyze_image", return_value=([title_line()], [title_line()])
                ),
            ):
                exit_code = SCRIPT.main([])

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.exists())
            self.assertFalse(temporary.exists())
            with fitz.open(output) as document:
                self.assertEqual(len(document), 1)
                self.assertIn("Searchable fixture text", document[0].get_text())
                self.assertEqual(document.get_toc(), [[1, "Fixture title", 1]])

    def test_existing_pdf_is_preserved_and_receives_missing_ocr_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_pdf = root / "source.pdf"
            source = fitz.open()
            first = source.new_page(width=612, height=792)
            first.draw_rect(first.rect, color=(0.2, 0.3, 0.7), fill=(0.2, 0.3, 0.7))
            second = source.new_page(width=612, height=792)
            second.insert_text(
                (72, 72), "This page already contains enough searchable source text."
            )
            source.save(source_pdf)
            source.close()

            output = root / "result-searchable.pdf"
            with (
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "titles.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    return_value=(searchable_page_bytes(), {"text": []}),
                ),
                mock.patch.object(
                    SCRIPT, "analyze_image", return_value=([title_line()], [title_line()])
                ),
                mock.patch.object(
                    SCRIPT,
                    "analyze_existing_text_page",
                    side_effect=lambda _image, native, _language: (native, native),
                ),
            ):
                exit_code = SCRIPT.main(
                    [
                        "--input-pdf",
                        str(source_pdf),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(any(root.glob(".*.tmp.pdf")))
            with fitz.open(output) as document:
                self.assertEqual(len(document), 2)
                self.assertAlmostEqual(document[0].rect.width, 612, places=1)
                self.assertAlmostEqual(document[0].rect.height, 792, places=1)
                self.assertIn("Searchable fixture text", document[0].get_text())
                self.assertIn("already contains", document[1].get_text())
                self.assertNotIn("Searchable fixture text", document[1].get_text())
                self.assertEqual(len(document.get_toc()), 2)

    def test_full_page_pdf_image_is_extracted_without_resampling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "original.png"
            Image.new("RGB", (640, 360), "purple").save(image_path)
            source_pdf = root / "source.pdf"
            source = fitz.open()
            page = source.new_page(width=800, height=450)
            page.insert_image(page.rect, filename=str(image_path))
            page.insert_text(
                (40, 40), "This searchable text already exists in the PDF page."
            )
            source.save(source_pdf)
            source.close()

            prepared = root / "prepared"
            files = SCRIPT.render_pdf_pages(source_pdf, prepared, 200)

            self.assertEqual(len(files), 1)
            with Image.open(prepared / files[0]) as extracted:
                self.assertEqual(extracted.size, (640, 360))

    def test_fully_searchable_pdf_skips_full_page_reocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_pdf = root / "source.pdf"
            output = root / "output.pdf"
            source = fitz.open()
            page = source.new_page(width=612, height=792)
            page.insert_text(
                (72, 72), "Existing searchable PDF title with enough native text."
            )
            source.save(source_pdf)
            source.close()

            with (
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "titles.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    side_effect=AssertionError("full-page OCR must be skipped"),
                ),
                mock.patch.object(
                    SCRIPT,
                    "analyze_existing_text_page",
                    side_effect=lambda _image, native, _language: (
                        native,
                        native + [title_line("Recovered targeted title", "upper-left")],
                    ),
                ),
            ):
                exit_code = SCRIPT.main(
                    [
                        "--input-pdf",
                        str(source_pdf),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            with fitz.open(output) as document:
                self.assertIn("Existing searchable", document[0].get_text())
                self.assertEqual(document.get_toc()[0][1], "Recovered targeted title")

    def test_mostly_searchable_pdf_skips_document_wide_reocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_pdf = root / "source.pdf"
            output = root / "output.pdf"
            source = fitz.open()
            for page_number in range(1, 6):
                page = source.new_page(width=320, height=180)
                if page_number < 5:
                    page.insert_text(
                        (24, 30),
                        f"Searchable title for existing PDF page {page_number}.",
                    )
            source.save(source_pdf)
            source.close()

            with (
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "titles.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    side_effect=AssertionError("document-wide OCR must be skipped"),
                ),
                mock.patch.object(
                    SCRIPT,
                    "analyze_existing_text_page",
                    side_effect=lambda _image, native, _language: (native, native),
                ),
            ):
                exit_code = SCRIPT.main(
                    [
                        "--input-pdf",
                        str(source_pdf),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            with fitz.open(output) as document:
                self.assertEqual(len(document), 5)
                self.assertEqual(document.get_toc()[-1][1], "Slide 5")

    def test_existing_pdf_cannot_overwrite_its_own_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source_pdf = Path(directory) / "source.pdf"
            source = fitz.open()
            source.new_page()
            source.save(source_pdf)
            source.close()
            original = source_pdf.read_bytes()

            with (
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
            ):
                exit_code = SCRIPT.main(
                    [
                        "--input-pdf",
                        str(source_pdf),
                        "--output",
                        str(source_pdf),
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(source_pdf.read_bytes(), original)

    def test_failure_does_not_overwrite_previous_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / "captures"
            captures.mkdir()
            Image.new("RGB", (800, 450), "white").save(captures / "Page_1.png")
            output = root / "result-searchable.pdf"
            output.write_bytes(b"previous-pdf")
            temporary = root / "result-searchable.tmp.pdf"

            with (
                mock.patch.object(SCRIPT, "CAPTURES_DIR", captures),
                mock.patch.object(SCRIPT, "OUTPUT_PDF", output),
                mock.patch.object(SCRIPT, "TEMP_OUTPUT_PDF", temporary),
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "titles.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    return_value=(searchable_page_bytes(), {"text": []}),
                ),
                mock.patch.object(
                    SCRIPT, "analyze_image", side_effect=RuntimeError("forced failure")
                ),
            ):
                exit_code = SCRIPT.main([])

            self.assertEqual(exit_code, 1)
            self.assertEqual(output.read_bytes(), b"previous-pdf")
            self.assertFalse(temporary.exists())

    def test_manual_override_has_priority_over_local_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / "captures"
            captures.mkdir()
            for page in (1, 2, 3):
                Image.new("RGB", (800, 450), "white").save(
                    captures / f"Page_{page}.png"
                )
            output = root / "result-searchable.pdf"
            temporary = root / "result-searchable.tmp.pdf"
            titles = root / "titles.json"
            titles.write_text('{"1": "Manual title"}', encoding="utf-8")
            with (
                mock.patch.object(SCRIPT, "CAPTURES_DIR", captures),
                mock.patch.object(SCRIPT, "OUTPUT_PDF", output),
                mock.patch.object(SCRIPT, "TEMP_OUTPUT_PDF", temporary),
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "unused.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    return_value=(searchable_page_bytes(), {"text": []}),
                ),
                mock.patch.object(
                    SCRIPT, "analyze_image", return_value=([title_line()], [title_line()])
                ),
            ):
                exit_code = SCRIPT.main(["--titles-file", str(titles)])

            self.assertEqual(exit_code, 0)
            with fitz.open(output) as document:
                self.assertEqual(
                    document.get_toc(),
                    [
                        [1, "Manual title", 1],
                        [1, "Fixture title", 2],
                        [1, "Fixture title", 3],
                    ],
                )

    def test_smart_dark_mode_detects_and_converts_only_black_on_white(self):
        eligible = Image.new("RGB", (400, 225), "white")
        drawing = ImageDraw.Draw(eligible)
        drawing.rectangle((25, 20, 260, 42), fill="black")
        assessment = SCRIPT.assess_dark_mode(eligible)
        self.assertTrue(assessment.eligible)

        converted = SCRIPT.apply_smart_dark_mode(eligible)
        self.assertEqual(converted.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(converted.getpixel((30, 25)), (255, 255, 255))

        already_dark = Image.new("RGB", (400, 225), "black")
        self.assertFalse(SCRIPT.assess_dark_mode(already_dark).eligible)
        colored = Image.new("RGB", (400, 225), "white")
        ImageDraw.Draw(colored).rectangle((25, 20, 260, 42), fill=(0, 30, 180))
        self.assertFalse(SCRIPT.assess_dark_mode(colored).eligible)

        photographic = Image.new("RGB", (400, 225), "white")
        photo_pixels = photographic.load()
        for y in range(65, 165):
            for x in range(80, 320):
                photo_pixels[x, y] = (
                    (x * 17 + y * 11) % 256,
                    (x * 7 + y * 23) % 256,
                    (x * 29 + y * 5) % 256,
                )
        ImageDraw.Draw(photographic).rectangle((25, 20, 260, 42), fill="black")
        self.assertFalse(SCRIPT.assess_dark_mode(photographic).eligible)

    def test_dark_mode_keeps_capture_pixel_dimensions_and_searchable_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / "captures"
            captures.mkdir()
            image = Image.new("RGB", (640, 360), "white")
            ImageDraw.Draw(image).rectangle((30, 25, 400, 55), fill="black")
            image.save(captures / "Page_1.png")
            output = root / "dark.pdf"

            with (
                mock.patch.object(SCRIPT, "DEFAULT_TITLES_FILE", root / "titles.json"),
                mock.patch.object(SCRIPT, "find_tesseract", return_value="tesseract"),
                mock.patch.object(SCRIPT, "choose_ocr_language", return_value="eng"),
                mock.patch.object(
                    SCRIPT,
                    "ocr_page",
                    return_value=(searchable_page_bytes(), {"text": []}),
                ),
                mock.patch.object(
                    SCRIPT, "analyze_image", return_value=([title_line()], [title_line()])
                ),
            ):
                exit_code = SCRIPT.main(
                    [
                        "--captures-dir",
                        str(captures),
                        "--output",
                        str(output),
                        "--dark-mode",
                    ]
                )

            self.assertEqual(exit_code, 0)
            with fitz.open(output) as document:
                self.assertIn("Searchable fixture text", document[0].get_text())
                images = document[0].get_images(full=True)
                self.assertEqual(len(images), 1)
                pixmap = fitz.Pixmap(document, images[0][0])
                self.assertEqual((pixmap.width, pixmap.height), (640, 360))

    def test_powershell_uses_local_ocr_and_offers_dark_mode(self):
        script = Path("screenshot-recorder.ps1").read_text(encoding="utf-8")
        self.assertIn("--input-pdf", script)
        self.assertIn("--dark-mode", script)
        self.assertIn('ocrArguments += "--output"', script)
        self.assertIn('sourceMode -eq "captures"', script)


if __name__ == "__main__":
    unittest.main()
