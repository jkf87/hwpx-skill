import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import summary_recipe


def example():
    return json.loads(summary_recipe.EXAMPLE_PATH.read_text(encoding="utf-8"))


def resolve_pointer(value, pointer):
    for part in pointer.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class SummaryRecipeTests(unittest.TestCase):
    def test_exact_mapping_and_no_input_mutation(self):
        content = example()
        content["sources"].append("두 번째 출처")
        content["facts"]["a/b~c"] = "목적"
        original = copy.deepcopy(content)
        spec, mapping = summary_recipe.compile_content(content)
        self.assertEqual(content, original)
        for row in mapping["mappings"]:
            self.assertEqual(resolve_pointer(content, row["source"]), row["text"])
            target = resolve_pointer(spec, row["target"])
            self.assertEqual(
                target[row["target_start"] : row["target_end"]], row["text"]
            )
        self.assertEqual(summary_recipe.compile_content(content), (spec, mapping))

    def test_strict_unsupported_keys_counts_and_plain_text(self):
        for key, value in [
            ("kind", "markdown"),
            ("direction", "vertical"),
            ("unknowns", ["date"]),
            ("output", "elsewhere.hwpx"),
            ("edges", []),
        ]:
            content = example()
            content[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "full v2"):
                summary_recipe.compile_content(content)
        for count in (0, 1, 7):
            content = example()
            content["stages"] = ["단계"] * count
            with self.subTest(count=count), self.assertRaises(ValueError):
                summary_recipe.compile_content(content)
        for text in ("a\nb", r"a\nb", "**강조**", " 공백", "가" * 13):
            content = example()
            content["stages"][0] = text
            with self.subTest(text=text), self.assertRaises(ValueError):
                summary_recipe.compile_content(content)
        content = example()
        content["sections"][0]["extra"] = True
        with self.assertRaises(ValueError):
            summary_recipe.compile_content(content)

    def test_actual_cli_build_content_and_reproducibility(self):
        content = example()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "content.json"
            source.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            hashes = []
            for name in ("run1", "run2"):
                out = base / name
                argv = [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "scripts/summary_recipe.py"),
                    str(source),
                    "--output-dir",
                    str(out),
                ]
                result = subprocess.run(argv, capture_output=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                quality = json.loads((out / "quality.json").read_text(encoding="utf-8"))
                self.assertTrue(quality["ok"])
                self.assertTrue(quality["published"])
                self.assertFalse(quality["recipe"]["one_page_verified"])
                self.assertEqual(quality["checks"]["render"]["status"], "not_run")
                self.assertEqual(quality["diagrams"][0]["direction"], "horizontal")
                log = json.loads(
                    (out / "execution-log.json").read_text(encoding="utf-8")
                )
                self.assertEqual(log["argv"], argv)
                self.assertEqual(log["stdout"], result.stdout)
                self.assertEqual(log["stderr"], result.stderr)
                self.assertEqual(log["exit_code"], result.returncode)
                self.assertEqual(
                    log["input_sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
                )
                self.assertEqual((out / "input.json").read_bytes(), source.read_bytes())
                with zipfile.ZipFile(out / "summary.hwpx") as package:
                    section = ET.fromstring(package.read("Contents/section0.xml"))
                    ns = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
                    text = "\n".join(
                        "".join(p.itertext()) for p in section.findall(".//hp:p", ns)
                    )
                    mapping = json.loads(
                        (out / "content-map.json").read_text(encoding="utf-8")
                    )
                    for row in mapping["mappings"]:
                        self.assertIn(row["text"], text)
                    self.assertTrue(section.findall(".//hp:tbl", ns))
                    self.assertFalse(section.findall(".//hp:pic", ns))
                hashes.append(
                    hashlib.sha256((out / "summary.hwpx").read_bytes()).hexdigest()
                )
            self.assertEqual(hashes[0], hashes[1])

    def test_existing_directory_and_file_are_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "source.json"
            source.write_text(json.dumps(example()), encoding="utf-8")
            folder = base / "existing"
            folder.mkdir()
            sentinel = folder / "keep.txt"
            sentinel.write_bytes(b"keep")
            for target in (folder, source, base / "missing-parent" / "output"):
                code, stdout, _ = summary_recipe.execute_recipe(
                    source, target, argv=["test"]
                )
                self.assertNotEqual(code, 0)
                self.assertFalse(json.loads(stdout)["report_saved"])
            self.assertEqual(sentinel.read_bytes(), b"keep")
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["keep.txt"])
            self.assertEqual(json.loads(source.read_text()), example())
            self.assertFalse((base / "missing-parent").exists())

    def test_schema_parse_and_build_failures_keep_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bad_fact = example()
            bad_fact["facts"] = {"missing": "문서에는없는필수사실"}
            cases = [
                ('{"title": "a", "title": "b"}', "validate_and_compile"),
                ('{"title": 7}', "validate_and_compile"),
                (json.dumps(bad_fact), "one_shot.build"),
            ]
            for i, (raw, phase) in enumerate(cases):
                source = base / f"source{i}.json"
                source.write_text(raw, encoding="utf-8")
                out = base / f"failure{i}"
                code, stdout, stderr = summary_recipe.execute_recipe(
                    source, out, argv=["test", str(source)]
                )
                self.assertEqual(code, 1)
                result = json.loads(stdout)
                self.assertFalse(result["published"])
                self.assertEqual(result["failure_phase"], phase)
                self.assertEqual((out / "input.json").read_bytes(), source.read_bytes())
                self.assertTrue((out / "quality.json").is_file())
                log = json.loads(
                    (out / "execution-log.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    (log["stdout"], log["stderr"], log["exit_code"]),
                    (stdout, stderr, code),
                )
                self.assertFalse((out / "summary.hwpx").exists())

    def test_discovery_is_not_a_build(self):
        for option in ("--schema", "--example"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "scripts/summary_recipe.py"),
                    option,
                ],
                capture_output=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)

    def test_engine_exception_preserves_both_streams(self):
        def failure(*args, **kwargs):
            print("engine progress")
            print("engine diagnostic", file=sys.stderr)
            raise RuntimeError("engine stopped")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "content.json"
            source.write_text(json.dumps(example()), encoding="utf-8")
            out = base / "failure"
            with mock.patch.object(
                summary_recipe.one_shot, "build", side_effect=failure
            ):
                code, stdout, stderr = summary_recipe.execute_recipe(
                    source, out, argv=["test"]
                )
            self.assertEqual(code, 1)
            log = json.loads((out / "execution-log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["engine_stdout"], "engine progress\n")
            self.assertEqual(log["engine_stderr"], "engine diagnostic\n")
            self.assertEqual((log["stdout"], log["stderr"]), (stdout, stderr))
            self.assertIn("engine stopped", json.loads(stdout)["errors"][0])
            self.assertTrue((out / "spec.json").is_file())
            self.assertTrue((out / "content-map.json").is_file())

    def test_quality_save_failure_is_partial_and_log_is_preserved(self):
        original_write = summary_recipe.write_new

        def fail_quality(path, text):
            if path.name == "quality.json":
                raise OSError("quality disk error")
            return original_write(path, text)

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "content.json"
            source.write_text(json.dumps(example()), encoding="utf-8")
            out = base / "partial"
            with mock.patch.object(
                summary_recipe, "write_new", side_effect=fail_quality
            ):
                code, stdout, stderr = summary_recipe.execute_recipe(
                    source, out, argv=["test"]
                )
            result = json.loads(stdout)
            self.assertEqual(
                (code, result["ok"], result["status"]), (2, False, "PARTIAL")
            )
            self.assertTrue(result["published"])
            self.assertFalse(result["report_saved"])
            self.assertTrue(result["execution_log_saved"])
            self.assertTrue((out / "summary.hwpx").is_file())
            self.assertFalse((out / "quality.json").exists())
            log = json.loads((out / "execution-log.json").read_text(encoding="utf-8"))
            self.assertEqual(
                (log["stdout"], log["stderr"], log["exit_code"]), (stdout, stderr, code)
            )

    def test_execution_log_save_failure_reports_partial_or_fail(self):
        original_write = summary_recipe.write_new

        def fail_log(path, text):
            if path.name == "execution-log.json":
                raise OSError("log disk error")
            return original_write(path, text)

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for valid in (True, False):
                source = base / f"content-{valid}.json"
                source.write_text(
                    json.dumps(example() if valid else {"title": 7}), encoding="utf-8"
                )
                out = base / f"result-{valid}"
                with mock.patch.object(
                    summary_recipe, "write_new", side_effect=fail_log
                ):
                    code, stdout, stderr = summary_recipe.execute_recipe(
                        source, out, argv=["test"]
                    )
                result = json.loads(stdout)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "PARTIAL" if valid else "FAIL")
                self.assertEqual(result["published"], valid)
                self.assertTrue(result["report_saved"])
                self.assertFalse(result["execution_log_saved"])
                self.assertIn("log disk error", stderr)
                self.assertFalse((out / "execution-log.json").exists())


if __name__ == "__main__":
    unittest.main()
