import unittest

from slide_title_detection import (
    OCRLine,
    detect_local_title,
    discover_repeated_templates,
    first_sentence,
    lines_from_tesseract,
    needs_rescue_ocr,
)


def line(
    text,
    left,
    top,
    *,
    width=0.45,
    height=0.035,
    confidence=95.0,
    block=1,
    paragraph=1,
    number=1,
    source="full",
):
    return OCRLine(
        text=text,
        left=left,
        top=top,
        right=min(1.0, left + width),
        bottom=min(1.0, top + height),
        height=height,
        confidence=confidence,
        block_num=block,
        par_num=paragraph,
        line_num=number,
        source=source,
    )


class LocalTitleDetectionTests(unittest.TestCase):
    def test_upper_left_position_beats_larger_body_line(self):
        lines = [
            line("Quarterly overview", 0.03, 0.04, source="upper-left"),
            line(
                "A message from the founders - Alex Morgan and Jordan Lee",
                0.03,
                0.19,
                height=0.045,
                paragraph=2,
            ),
            line("Operational resilience.", 0.03, 0.31, height=0.052, paragraph=3),
        ]
        decision = detect_local_title(lines, set(), 1)
        self.assertEqual(decision.title, "Quarterly overview")
        self.assertEqual(decision.strategy, "upper-left")

    def test_sparse_centered_divider_preserves_decorative_prefix(self):
        decision = detect_local_title(
            [line("[AGENDA]", 0.34, 0.52, width=0.30, height=0.075)],
            set(),
            1,
        )
        self.assertEqual(decision.title, "[AGENDA]")
        self.assertEqual(decision.strategy, "section")

    def test_multiline_upper_left_title_is_joined(self):
        lines = [
            line("Digital investigation", 0.04, 0.05, height=0.04, source="upper-left"),
            line(
                "and incident response",
                0.04,
                0.095,
                height=0.039,
                number=2,
                source="upper-left",
            ),
            line("Body text starts here", 0.04, 0.30, paragraph=2),
        ]
        decision = detect_local_title(lines, set(), 1)
        self.assertEqual(
            decision.title, "Digital investigation and incident response"
        )

    def test_multiline_centered_section_uses_center_alignment(self):
        lines = [
            line("RESPONSE", 0.30, 0.40, width=0.40, height=0.060),
            line("STRATEGY", 0.38, 0.475, width=0.24, height=0.058, number=2),
            line("2026", 0.46, 0.72, width=0.08, height=0.020, paragraph=2),
        ]
        decision = detect_local_title(lines, set(), 2)
        self.assertEqual(decision.title, "RESPONSE STRATEGY")
        self.assertEqual(decision.strategy, "section")

    def test_cover_title_does_not_absorb_smaller_subtitle(self):
        lines = [
            line("Annual report", 0.31, 0.35, width=0.38, height=0.070),
            line("Results and outlook", 0.34, 0.45, width=0.32, height=0.026, number=2),
            line("June 2026", 0.44, 0.68, width=0.12, height=0.018, paragraph=2),
        ]
        decision = detect_local_title(lines, set(), 1)
        self.assertEqual(decision.title, "Annual report")
        self.assertEqual(decision.strategy, "section")

    def test_top_center_header_is_used_on_content_slide(self):
        lines = [line("Target architecture", 0.34, 0.04, width=0.30, source="header")]
        for index in range(7):
            lines.append(
                line(
                    f"Content line number {index}",
                    0.08,
                    0.30 + index * 0.06,
                    paragraph=10 + index,
                )
            )
        decision = detect_local_title(lines, set(), 1)
        self.assertEqual(decision.title, "Target architecture")
        self.assertEqual(decision.strategy, "header")

    def test_right_sidebar_cannot_beat_top_left_title(self):
        lines = [
            line("Key results", 0.04, 0.06, width=0.30, height=0.038),
            line("TAKEAWAY", 0.76, 0.03, width=0.20, height=0.065),
            line("Revenue increased by twelve percent.", 0.05, 0.34, paragraph=2),
        ]
        self.assertEqual(
            detect_local_title(lines, set(), 1).title, "Key results"
        )

    def test_small_kicker_above_title_does_not_win(self):
        lines = [
            line("ACME LAB", 0.04, 0.02, width=0.12, height=0.018),
            line("Threat landscape", 0.04, 0.10, width=0.46, height=0.052, paragraph=2),
            line("Supporting paragraph content.", 0.04, 0.36, paragraph=3),
        ]
        self.assertEqual(
            detect_local_title(lines, set(), 1).title, "Threat landscape"
        )

    def test_repeated_template_is_penalized_with_minor_ocr_jitter(self):
        pages = []
        brands = ("ACME CORPORATION", "ACME CORPORATlON", "ACME CORPORATION", "ACME CORPORATON")
        for index, brand in enumerate(brands):
            pages.append(
                [
                    line(brand, 0.02, 0.02, width=0.18, height=0.025),
                    line(
                        f"Unique slide title {index}",
                        0.04,
                        0.10,
                        height=0.042,
                        paragraph=2,
                    ),
                    line("Supporting body paragraph", 0.04, 0.35, paragraph=3),
                ]
            )
        repeated = discover_repeated_templates(pages)
        decision = detect_local_title(pages[0], repeated, 1)
        self.assertEqual(decision.title, "Unique slide title 0")

    def test_numbered_titles_are_not_grouped_as_repeated_templates(self):
        pages = [
            [line(f"Chapter {index}", 0.04, 0.06, height=0.045)]
            for index in range(1, 6)
        ]
        repeated = discover_repeated_templates(pages)
        for page in pages:
            self.assertEqual(
                detect_local_title(page, repeated, 1).strategy,
                "upper-left",
            )

    def test_dense_centered_quote_is_not_mistaken_for_section(self):
        lines = [line("Summary", 0.04, 0.05, width=0.24, height=0.040)]
        lines.extend(
            line(
                f"Detailed body line {index} with useful information.",
                0.20,
                0.28 + index * 0.055,
                width=0.60,
                height=0.035 if index != 2 else 0.052,
                paragraph=10 + index,
            )
            for index in range(8)
        )
        self.assertEqual(detect_local_title(lines, set(), 1).title, "Summary")

    def test_first_sentence_of_first_paragraph_is_fallback(self):
        lines = [
            line(
                "This is the first sentence. This is the second one.",
                0.38,
                0.42,
                paragraph=4,
            )
        ]
        decision = detect_local_title(lines, set(), 3)
        self.assertEqual(decision.title, "This is the first sentence.")
        self.assertEqual(decision.strategy, "paragraph")

    def test_pdf_text_can_supply_paragraph_fallback(self):
        lines = [
            line(
                "Native PDF paragraph begins here. Another sentence follows.",
                0.40,
                0.44,
                paragraph=8,
                source="pdf-text",
            )
        ]
        decision = detect_local_title(lines, set(), 4)
        self.assertEqual(decision.title, "Native PDF paragraph begins here.")
        self.assertEqual(decision.source, "pdf-text")

    def test_bullet_is_skipped_before_first_real_paragraph(self):
        lines = [
            line("• Bullet item", 0.40, 0.40, paragraph=1),
            line(
                "The explanatory paragraph starts now. More details follow.",
                0.40,
                0.52,
                paragraph=2,
            ),
        ]
        decision = detect_local_title(lines, set(), 5)
        self.assertEqual(decision.title, "The explanatory paragraph starts now.")

    def test_page_numbers_and_footer_are_ignored(self):
        lines = [
            line("12 / 30", 0.46, 0.03, width=0.08),
            line("https://example.test", 0.04, 0.93, width=0.25, paragraph=2),
        ]
        self.assertEqual(detect_local_title(lines, set(), 12).title, "Slide 12")

    def test_empty_slide_uses_numbered_fallback(self):
        decision = detect_local_title([], set(), 7)
        self.assertEqual(decision.title, "Slide 7")
        self.assertEqual(decision.strategy, "fallback")

    def test_first_sentence_keeps_closing_quote(self):
        self.assertEqual(
            first_sentence("« First sentence. » Second sentence."),
            "« First sentence. »",
        )
        self.assertEqual(
            first_sentence("“First sentence.” Second sentence."),
            "“First sentence.”",
        )

    def test_long_fallback_is_truncated_on_word_boundary(self):
        text = " ".join(["presentation"] * 20)
        title = first_sentence(text)
        self.assertLessEqual(len(title), 120)
        self.assertFalse(title.endswith("presentatio"))

    def test_tesseract_crop_coordinates_are_mapped_to_full_page(self):
        data = {
            "text": ["Title"],
            "conf": ["96"],
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
            "left": [20],
            "top": [10],
            "width": [100],
            "height": [20],
        }
        result = lines_from_tesseract(
            data,
            1000,
            500,
            source="header",
            offset_x=100,
            offset_y=50,
        )
        self.assertAlmostEqual(result[0].left, 0.12)
        self.assertAlmostEqual(result[0].top, 0.12)
        self.assertEqual(result[0].source, "header")

    def test_rescue_ocr_is_only_requested_for_weak_layout(self):
        confident = [
            line("Strong title", 0.03, 0.03, height=0.05, source="upper-left"),
            line("Body", 0.05, 0.40, paragraph=2),
        ]
        self.assertFalse(needs_rescue_ocr(confident))
        self.assertTrue(needs_rescue_ocr([line("Body paragraph", 0.40, 0.50)]))


if __name__ == "__main__":
    unittest.main()
