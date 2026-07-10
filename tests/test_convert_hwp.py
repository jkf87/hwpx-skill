#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from importlib import util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, Protocol, cast
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CONVERTER = SCRIPTS / "convert_hwp.py"
EXPORT_PATCHER = SCRIPTS / "hwpx_export_patch.py"
VALIDATOR = SCRIPTS / "validate.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "labor_template.hwp"


class ConvertFunction(Protocol):
    def __call__(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        fix_char_borders: bool = True,
        fix_text_direction: bool = True,
    ) -> str: ...


class NormalizeFunction(Protocol):
    def __call__(self, source_file: BinaryIO, destination_path: Path) -> None: ...


def load_module(name: str, path: Path) -> ModuleType:
    loader = SourceFileLoader(name, str(path))
    spec = util.spec_from_loader(name, loader)
    assert spec is not None
    module = util.module_from_spec(spec)
    loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPTS))
CONVERTER_MODULE = load_module("convert_hwp", CONVERTER)
PATCHER_MODULE = load_module("hwpx_export_patch_test", EXPORT_PATCHER)
CONVERT = cast(ConvertFunction, getattr(CONVERTER_MODULE, "convert"))
NORMALIZE_EXPORTED_HWPX = cast(
    NormalizeFunction,
    getattr(PATCHER_MODULE, "normalize_exported_hwpx"),
)


def run_converter(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CONVERTER), *arguments],
        capture_output=True,
        text=True,
    )


def section_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("Contents/section0.xml").decode("utf-8")


def test_source_page_geometry_is_preserved() -> None:
    expected = {
        "width": "59528",
        "height": "84188",
        "left": "5385",
        "right": "5385",
        "top": "5669",
        "bottom": "2834",
        "header": "0",
        "footer": "0",
        "gutter": "0",
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "converted.hwpx"
        converted = run_converter(str(FIXTURE), "-o", str(output))
        assert converted.returncode == 0, converted.stderr
        root = ElementTree.fromstring(section_text(output))
        page_pr = next(
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "pagePr"
        )
        margin = next(
            element
            for element in page_pr
            if element.tag.rsplit("}", 1)[-1] == "margin"
        )
        actual = {
            "width": page_pr.attrib["width"],
            "height": page_pr.attrib["height"],
            **{key: margin.attrib[key] for key in expected if key not in {"width", "height"}},
        }
        assert actual == expected


def test_table_linebreaks_and_strict_validation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "converted.hwpx"
        converted = run_converter(str(FIXTURE), "-o", str(output))
        assert converted.returncode == 0, converted.stderr
        xml = section_text(output)
        assert "1. Term of\nLabor\ncontract" in xml
        assert "1. Term ofLaborcontract" not in xml
        assert "<hp:linesegarray" in xml
        validated = subprocess.run(
            [sys.executable, str(VALIDATOR), str(output)],
            capture_output=True,
            text=True,
        )
        assert validated.returncode == 0, validated.stdout + validated.stderr
        with zipfile.ZipFile(output) as archive:
            preview = archive.read("Preview/PrvText.txt").decode("utf-8")
        assert "1. Term of" in preview


def test_info_contract_marks_unavailable_metadata() -> None:
    inspected = run_converter(str(FIXTURE), "--info", "--json")
    assert inspected.returncode == 0, inspected.stderr
    result = cast(dict[str, object], json.loads(inspected.stdout))
    assert result["version"] == "5.0.3.4"
    assert result["section_count"] == 1
    assert result["page_count"] == 2
    assert result["paragraph_count"] == 2
    assert result["title"] is None
    assert result["metadata_available"] is False


def test_same_source_destination_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        collision = Path(temp_dir) / "collision.hwp"
        _ = collision.write_bytes(FIXTURE.read_bytes())
        original_bytes = collision.read_bytes()
        rejected = run_converter(str(collision), "-o", str(collision))
        assert rejected.returncode == 1
        assert collision.read_bytes() == original_bytes


def test_failed_conversion_preserves_existing_output() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        invalid = Path(temp_dir) / "invalid.hwp"
        output = Path(temp_dir) / "existing.hwpx"
        _ = invalid.write_bytes(b"not a compound file")
        _ = output.write_bytes(b"keep this output")
        failed = run_converter(str(invalid), "-o", str(output))
        assert failed.returncode == 1
        assert output.read_bytes() == b"keep this output"
        assert not list(output.parent.glob(f".{output.name}.*"))


def test_legacy_cli_forms_remain_accepted() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "converted.hwpx"
        converted = run_converter(
            str(FIXTURE),
            f"--output={output}",
            "--keep-char-borders",
        )
        assert converted.returncode == 0, converted.stderr
        assert output.exists()


def test_legacy_python_arguments_remain_accepted() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "converted.hwpx"
        result = CONVERT(FIXTURE, output, False, False)
        assert Path(result) == output
        assert output.exists()


def test_post_export_picture_and_manifest_patches() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = Path(temp_dir) / "synthetic.hwpx"
        content = (
            b'<opf:package xmlns:opf="urn:test"><opf:manifest>'
            b'<opf:item id="image1" href="BinData/image1.png" '
            b'media-type="image/png"/></opf:manifest></opf:package>'
        )
        section = (
            b'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            b'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b"<hp:p><hp:run><hp:t>preview text</hp:t></hp:run>"
            b'<hp:pic><hp:orgSz width="0" height="0"/>'
            b'<hp:curSz width="900" height="600"/>'
            b'<hp:imgClip left="0" right="1200" top="0" bottom="800"/>'
            b'<hp:imgDim dimwidth="0" dimheight="0"/></hp:pic>'
            b"<hp:linesegarray><hp:lineseg/></hp:linesegarray></hp:p>"
            b'<hp:p><hp:run charPrIDRef="0"><hp:t> </hp:t>'
            b'<hp:tbl id="1"></hp:tbl></hp:run></hp:p>'
            b"<hp:p><hp:run><hp:t>second paragraph</hp:t></hp:run></hp:p>"
            b"</hs:sec>"
        )
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mimetype", "application/hwp+zip", zipfile.ZIP_STORED)
            archive.writestr("Contents/content.hpf", content)
            archive.writestr("Contents/section0.xml", section)
            archive.writestr("Preview/PrvText.txt", b"\r\n")
        normalized_path = Path(temp_dir) / "normalized.hwpx"
        with archive_path.open("rb") as source_file:
            NORMALIZE_EXPORTED_HWPX(source_file, normalized_path)
        with zipfile.ZipFile(normalized_path) as archive:
            patched_hpf = archive.read("Contents/content.hpf")
            patched_section = archive.read("Contents/section0.xml")
            preview = archive.read("Preview/PrvText.txt")
            assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert b'isEmbeded="1"' in patched_hpf
        assert b'<hp:orgSz width="1200" height="800"/>' in patched_section
        assert b'<hp:imgDim dimwidth="1200" dimheight="800"/>' in patched_section
        assert b"<hp:linesegarray" in patched_section
        assert b"<hp:t> </hp:t><hp:tbl" not in patched_section
        assert b"xmlns:hwpunitchar=" in patched_section
        assert preview == b"preview text\r\nsecond paragraph\r\n"


def test_node18_esm_boundary() -> None:
    package = cast(
        dict[str, str],
        json.loads((SCRIPTS / "vendor" / "rhwp" / "package.json").read_text()),
    )
    assert package == {"type": "module"}
    node18 = os.environ.get("NODE18_BIN")
    if node18:
        completed = subprocess.run(
            [node18, str(SCRIPTS / "rhwp_convert.mjs"), "--info", str(FIXTURE)],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.startswith("hwp\n5.0.3.4\n")


TEST_CASES = (
    test_source_page_geometry_is_preserved,
    test_table_linebreaks_and_strict_validation,
    test_info_contract_marks_unavailable_metadata,
    test_same_source_destination_is_rejected,
    test_failed_conversion_preserves_existing_output,
    test_legacy_cli_forms_remain_accepted,
    test_legacy_python_arguments_remain_accepted,
    test_post_export_picture_and_manifest_patches,
    test_node18_esm_boundary,
)


def main() -> int:
    for test_case in TEST_CASES:
        test_case()
    print(f"{len(TEST_CASES)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
