"""rhwp render engine: Windows-less page evidence that never claims Hancom fidelity."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_hwpx

SAMPLE = ROOT / "assets" / "gyehoek-reference.hwpx"


class EngineSelectionTests(unittest.TestCase):
    def test_auto_is_hancom_only_on_windows(self):
        with patch("render_hwpx.os.name", "nt"):
            self.assertEqual(render_hwpx.resolve_engine("auto"), "hancom")
        with patch("render_hwpx.os.name", "posix"):
            self.assertEqual(render_hwpx.resolve_engine("auto"), "rhwp")
        self.assertEqual(render_hwpx.resolve_engine("rhwp"), "rhwp")
        with self.assertRaises(ValueError):
            render_hwpx.resolve_engine("pdf")

    def test_svg_scaling_touches_only_root_size(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="200" '
            'viewBox="0 0 100 200"><rect width="10" height="20" stroke-width="1"/></svg>'
        )
        scaled = render_hwpx._root_svg_scaled(svg, 2)
        self.assertIn('width="200.00" height="400.00" viewBox="0 0 100 200"', scaled)
        self.assertIn('<rect width="10" height="20" stroke-width="1"/>', scaled)


class RhwpRenderTests(unittest.TestCase):
    def test_missing_node_is_not_run(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("render_hwpx.shutil.which", return_value=None):
                result = render_hwpx.render_document(
                    SAMPLE, Path(td) / "out", expected_pages=1, engine="rhwp"
                )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "not_run")
        self.assertFalse(result["render_ok"])
        self.assertIsNone(result["page_count_check"]["ok"])

    @unittest.skipUnless(shutil.which("node"), "node unavailable")
    def test_renders_pages_and_labels_fidelity(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            result = render_hwpx.render_document(SAMPLE, out, engine="rhwp")
            self.assertTrue(result["render_ok"], result.get("message"))
            self.assertEqual(result["engine"], "rhwp")
            self.assertIn("not a Hancom render", result["fidelity"])
            self.assertEqual(result["visual_review"]["status"], "not_run")
            pages = result["rendered_pages"]
            self.assertGreaterEqual(pages, 1)
            self.assertEqual(len(result["page_svgs"]), pages)
            for svg in result["page_svgs"]:
                self.assertTrue(Path(svg).read_text(encoding="utf-8").startswith("<svg"))
            if result["image_converter"]:
                self.assertTrue(result["ok"])
                self.assertEqual(len(result["page_images"]), pages)
                for png in result["page_images"]:
                    self.assertEqual(Path(png).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            else:
                self.assertEqual(result["status"], "partial")
            saved = json.loads((out / "render.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["rendered_pages"], pages)

            wrong = render_hwpx.render_document(
                SAMPLE, Path(td) / "wrong", expected_pages=pages + 1, engine="rhwp"
            )
            self.assertFalse(wrong["ok"])
            self.assertEqual(wrong["page_count_check"]["status"], "failed")
            self.assertEqual(wrong["page_count_check"]["actual"], pages)

    @unittest.skipUnless(shutil.which("node"), "node unavailable")
    def test_page_count_works_without_png_converter(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("render_hwpx.svg_to_png", return_value=None):
                result = render_hwpx.render_document(
                    SAMPLE, Path(td) / "out", expected_pages=1, engine="rhwp"
                )
        self.assertFalse(result["ok"])
        self.assertTrue(result["render_ok"])
        self.assertEqual(result["page_images"], [])
        self.assertIsNotNone(result["page_count_check"]["actual"])


if __name__ == "__main__":
    unittest.main()
