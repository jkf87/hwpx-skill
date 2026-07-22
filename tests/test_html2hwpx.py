import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "html2hwpx.py"
FIXTURE = ROOT / "tests" / "fixtures" / "k-teacher-design.html"


def load_module():
    spec = importlib.util.spec_from_file_location("html2hwpx", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HtmlToHwpxTest(unittest.TestCase):
    def test_parse_components(self):
        module = load_module()
        plan = module.parse_html(FIXTURE)
        types = [block["type"] for block in plan["blocks"]]
        self.assertEqual(plan["title"], "기후 변화 탐구 활동지")
        self.assertIn("doc_header", types)
        self.assertIn("identity", types)
        self.assertIn("goal_card", types)
        self.assertIn("caption", types)
        self.assertIn("student_task", types)
        self.assertIn("source_card", types)
        self.assertIn("sentence_support", types)
        self.assertIn("answer_box", types)
        self.assertIn("fill_table", types)
        self.assertIn("data_table", types)
        self.assertIn("page_break", types)
        self.assertIn("exit_ticket", types)
        answer = next(block for block in plan["blocks"] if block["type"] == "answer_box")
        self.assertEqual(answer["row_heights_mm"], [8.0, 8.0, 8.0])

    def test_end_to_end(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "result.hwpx"
            xml_dir = tmp_path / "xml"
            module.convert(FIXTURE, output, keep_xml=xml_dir, creator="test")
            self.assertTrue(output.is_file())
            self.assertTrue((xml_dir / "design-plan.xml").is_file())
            self.assertTrue((xml_dir / "section0.xml").is_file())
            etree.parse(str(xml_dir / "section0.xml"))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist()[0], "mimetype")
                header = archive.read("Contents/header.xml").decode("utf-8")
                section = archive.read("Contents/section0.xml").decode("utf-8")
            for color in (
                "#243447", "#19324D", "#128277", "#3267D6", "#E86A5A",
                "#D4DEE9", "#EDF3FA", "#F0F8F6", "#FFF4E8", "#A65E15", "#7C8DA0",
            ):
                self.assertIn(color, header)
            self.assertIn("<hc:fillBrush>", header)
            self.assertNotIn("<hh:fillBrush>", header)
            self.assertIn("기후 변화 탐구 활동지", section)
            self.assertIn("EXIT TICKET · 가장 어려운 사례", section)
            self.assertIn("STEP", section)
            self.assertGreaterEqual(section.count('<hp:rect '), 4)
            self.assertGreaterEqual(section.count('ratio="24"'), 4)
            self.assertIn("rounded worksheet card", section)
            self.assertIn('pageBreak="1"', section)
            self.assertNotIn("Workflow K", section)


if __name__ == "__main__":
    unittest.main()
