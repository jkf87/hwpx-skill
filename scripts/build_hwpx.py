#!/usr/bin/env python3
"""Build an HWPX document from templates and XML overrides.

Assembles a valid HWPX file by:
1. Copying the base template
2. Optionally overlaying a document-type template (gonmun, report, minutes)
3. Optionally overriding header.xml and/or section0.xml with custom files
4. Optionally setting metadata (title, creator)
5. Validating XML well-formedness
6. Packaging as HWPX (ZIP with mimetype first, ZIP_STORED)

Usage:
    # Empty document from base template
    python build_hwpx.py --output result.hwpx

    # Using a document-type template
    python build_hwpx.py --template gonmun --output result.hwpx

    # Custom section XML override
    python build_hwpx.py --template gonmun --section my_section0.xml --output result.hwpx

    # Custom header and section
    python build_hwpx.py --header my_header.xml --section my_section0.xml --output result.hwpx

    # With metadata
    python build_hwpx.py --template gonmun --section my.xml --title "제목" --creator "작성자" --output result.hwpx
"""

from __future__ import annotations  # Python 3.9 호환: str | None 등 어노테이션 지연 평가

import argparse
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from lxml import etree

# Resolve paths relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
BASE_DIR = TEMPLATES_DIR / "base"

# templates/ 하위의 실제 디렉터리를 동적으로 인식(base 제외).
# 하드코딩 목록은 government/proposal/gonmun2025 등 신규 템플릿을 누락시켰음.
AVAILABLE_TEMPLATES = sorted(
    p.name for p in TEMPLATES_DIR.iterdir()
    if p.is_dir() and p.name != "base"
) if TEMPLATES_DIR.is_dir() else ["gonmun", "report", "minutes"]


def validate_xml(filepath: Path) -> None:
    """Check that an XML file is well-formed. Raises on error."""
    try:
        etree.parse(str(filepath))
    except etree.XMLSyntaxError as e:
        raise SystemExit(f"Malformed XML in {filepath.name}: {e}")


def update_metadata(content_hpf: Path, title: str | None, creator: str | None) -> None:
    """Update title and/or creator in content.hpf."""
    if not title and not creator:
        return

    tree = etree.parse(str(content_hpf))
    root = tree.getroot()
    ns = {"opf": "http://www.idpf.org/2007/opf/"}

    if title:
        title_el = root.find(".//opf:title", ns)
        if title_el is not None:
            title_el.text = title

    now = datetime.now(timezone.utc)
    iso_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for meta in root.findall(".//opf:meta", ns):
        name = meta.get("name", "")
        if creator and name == "creator":
            meta.text = creator
        elif creator and name == "lastsaveby":
            meta.text = creator
        elif name == "CreatedDate":
            meta.text = iso_now
        elif name == "ModifiedDate":
            meta.text = iso_now
        elif name == "date":
            meta.text = now.strftime("%Y년 %m월 %d일")

    etree.indent(root, space="  ")
    tree.write(
        str(content_hpf),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )


def pack_hwpx(input_dir: Path, output_path: Path) -> None:
    """Create HWPX archive with mimetype as first entry (ZIP_STORED)."""
    mimetype_file = input_dir / "mimetype"
    if not mimetype_file.is_file():
        raise SystemExit(f"Missing 'mimetype' in {input_dir}")

    all_files = sorted(
        p.relative_to(input_dir).as_posix()
        for p in input_dir.rglob("*")
        if p.is_file()
    )

    with ZipFile(output_path, "w", ZIP_DEFLATED) as zf:
        zf.write(mimetype_file, "mimetype", compress_type=ZIP_STORED)
        for rel_path in all_files:
            if rel_path == "mimetype":
                continue
            zf.write(input_dir / rel_path, rel_path, compress_type=ZIP_DEFLATED)


def validate_hwpx(hwpx_path: Path) -> list[str]:
    """Quick structural validation of the output HWPX."""
    errors: list[str] = []
    required = [
        "mimetype",
        "Contents/content.hpf",
        "Contents/header.xml",
        "Contents/section0.xml",
    ]

    try:
        from zipfile import BadZipFile
        zf = ZipFile(hwpx_path, "r")
    except BadZipFile:
        return [f"Not a valid ZIP: {hwpx_path}"]

    with zf:
        names = zf.namelist()
        for r in required:
            if r not in names:
                errors.append(f"Missing: {r}")

        if "mimetype" in names:
            content = zf.read("mimetype").decode("utf-8").strip()
            if content != "application/hwp+zip":
                errors.append(f"Bad mimetype content: {content}")
            if names[0] != "mimetype":
                errors.append("mimetype is not the first ZIP entry")
            info = zf.getinfo("mimetype")
            if info.compress_type != ZIP_STORED:
                errors.append("mimetype is not ZIP_STORED")

        for name in names:
            if name.endswith(".xml") or name.endswith(".hpf"):
                try:
                    etree.fromstring(zf.read(name))
                except etree.XMLSyntaxError as e:
                    errors.append(f"Malformed XML: {name}: {e}")

    return errors


def _write_preview(work: Path) -> None:
    """본문(section0.xml) 텍스트로 Preview/PrvText.txt를 생성한다.

    한컴은 저장 시 본문을 반영한 미리보기(PrvText)를 만든다. 템플릿의 빈
    placeholder를 그대로 두면 fill_hwpx의 raw 탐지(미리보기 미반영 + 줄배치
    부재)에 걸려 check --strict가 '빈 페이지' 위험으로 차단한다. 그래서 조립
    단계에서 실제 본문을 반영한 미리보기를 써 둔다(md2hwpx·gonmun 등 공통).
    """
    section = work / "Contents" / "section0.xml"
    if not section.is_file():
        return
    try:
        tree = etree.parse(str(section))
    except Exception:  # noqa: BLE001
        return
    parts = [el.text for el in tree.iter()
             if etree.QName(el).localname == "t" and el.text]
    preview = "\n".join(parts).strip()
    if not preview:
        return
    prev_dir = work / "Preview"
    prev_dir.mkdir(parents=True, exist_ok=True)
    (prev_dir / "PrvText.txt").write_text(preview, encoding="utf-8")


def build(
    template: str | None,
    header_override: Path | None,
    section_override: Path | None,
    title: str | None,
    creator: str | None,
    output: Path,
) -> None:
    """Main build logic."""

    if not BASE_DIR.is_dir():
        raise SystemExit(f"Base template not found: {BASE_DIR}")

    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir) / "build"

        # 1. Copy base template
        shutil.copytree(BASE_DIR, work)

        # 2. Apply template overlay
        if template:
            overlay_dir = TEMPLATES_DIR / template
            if not overlay_dir.is_dir():
                raise SystemExit(
                    f"Template '{template}' not found. "
                    f"Available: {', '.join(AVAILABLE_TEMPLATES)}"
                )
            for overlay_file in overlay_dir.iterdir():
                if overlay_file.is_file() and overlay_file.suffix == ".xml":
                    dest = work / "Contents" / overlay_file.name
                    shutil.copy2(overlay_file, dest)

        # 3. Apply custom overrides
        if header_override:
            if not header_override.is_file():
                raise SystemExit(f"Header file not found: {header_override}")
            shutil.copy2(header_override, work / "Contents" / "header.xml")

        if section_override:
            if not section_override.is_file():
                raise SystemExit(f"Section file not found: {section_override}")
            shutil.copy2(section_override, work / "Contents" / "section0.xml")

        # 4. Update metadata
        update_metadata(work / "Contents" / "content.hpf", title, creator)

        # 5. Validate all XML files
        for xml_file in work.rglob("*.xml"):
            validate_xml(xml_file)
        for hpf_file in work.rglob("*.hpf"):
            validate_xml(hpf_file)

        # 5.5 본문 기반 미리보기 생성 (raw '빈 페이지' 게이트 통과)
        _write_preview(work)

        # 6. Pack
        pack_hwpx(work, output)

    # 7. Final validation
    errors = validate_hwpx(output)
    if errors:
        print(f"WARNING: {output} has issues:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    else:
        print(f"VALID: {output}")
        print(f"  Template: {template or 'base'}")
        if header_override:
            print(f"  Header: {header_override}")
        if section_override:
            print(f"  Section: {section_override}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build HWPX document from templates and XML overrides"
    )
    parser.add_argument(
        "--template", "-t",
        choices=AVAILABLE_TEMPLATES,
        help="Document type template to use as overlay",
    )
    parser.add_argument(
        "--header",
        type=Path,
        help="Custom header.xml to override",
    )
    parser.add_argument(
        "--section",
        type=Path,
        help="Custom section0.xml to override",
    )
    parser.add_argument(
        "--title",
        help="Document title (updates content.hpf metadata)",
    )
    parser.add_argument(
        "--creator",
        help="Document creator (updates content.hpf metadata)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output .hwpx file path",
    )
    args = parser.parse_args()

    build(
        template=args.template,
        header_override=args.header,
        section_override=args.section,
        title=args.title,
        creator=args.creator,
        output=args.output,
    )


if __name__ == "__main__":
    main()
