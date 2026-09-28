"""Check that tall slide + notes captures fit one landscape DOCX page."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "img-2-docx.py"


class DocxFitTests(unittest.TestCase):
    def test_tall_composite_fits_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(SCRIPT, root / SCRIPT.name)
            captures = root / "captures"
            captures.mkdir()
            Image.new("RGB", (1280, 1500), "white").save(captures / "Page_1.png")

            subprocess.run(
                [sys.executable, str(root / SCRIPT.name)],
                input="",
                text=True,
                check=True,
                capture_output=True,
            )
            document = Document(root / "result.docx")
            self.assertEqual(len(document.inline_shapes), 1)
            shape = document.inline_shapes[0]
            section = document.sections[0]
            self.assertLessEqual(shape.width, section.page_width)
            self.assertLessEqual(shape.height, int(section.page_height * 0.93))
            self.assertAlmostEqual(shape.width / shape.height, 1280 / 1500, places=3)


if __name__ == "__main__":
    unittest.main()
