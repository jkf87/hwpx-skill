from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence
from xml.etree import ElementTree


WHITESPACE_TABLE_PREFIX_RE = re.compile(
    rb"<hp:t\b[^>]*>[\t\r\n ]*</hp:t>(?=<hp:tbl\b)"
)
IMAGE_ITEM_RE = re.compile(
    rb"<opf:item\b(?=[^>]*\bmedia-type=[\"']image/)[^>]*?/>",
    re.IGNORECASE,
)
PICTURE_RE = re.compile(rb"<hp:pic\b.*?</hp:pic>", re.DOTALL)
PAGE_PR_RE = re.compile(rb"<hp:pagePr\b[^>]*>.*?</hp:pagePr>", re.DOTALL)
SECTION_NAME_RE = re.compile(r"^Contents/section(\d+)\.xml$")


def _mark_embedded_images(content_hpf: bytes) -> bytes:
    def add_attribute(match: re.Match[bytes]) -> bytes:
        item = match.group(0)
        if re.search(rb"\bisEmbeded\s*=", item, re.IGNORECASE):
            return item
        return item[:-2].rstrip() + b' isEmbeded="1"/>'

    return IMAGE_ITEM_RE.sub(add_attribute, content_hpf)


def _owned_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []

    def collect(element: ElementTree.Element) -> None:
        for child in element:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name == "p":
                continue
            if local_name == "t":
                parts.extend(child.itertext())
            else:
                collect(child)

    collect(paragraph)
    return "".join(parts)


def _preview_text(section_payloads: list[bytes]) -> bytes:
    paragraphs: list[str] = []
    for payload in section_payloads:
        root = ElementTree.fromstring(payload)
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "p":
                text = _owned_text(element)
                if text:
                    paragraphs.append(text)
    return ("\r\n".join(paragraphs) + "\r\n").encode("utf-8")


def _patch_picture_sizes(section_xml: bytes) -> bytes:
    def patch_picture(match: re.Match[bytes]) -> bytes:
        picture = match.group(0)
        clip = re.search(rb"<hp:imgClip\b([^>]*)/>", picture)
        current = re.search(rb"<hp:curSz\b([^>]*)/>", picture)
        source = clip.group(1) if clip else (current.group(1) if current else b"")
        width = re.search(rb'\b(?:right|width)="(\d+)"', source)
        height = re.search(rb'\b(?:bottom|height)="(\d+)"', source)
        if not width or not height:
            return picture
        dimensions = width.group(1), height.group(1)
        picture = re.sub(
            rb'<hp:orgSz\s+width="0"\s+height="0"\s*/>',
            b'<hp:orgSz width="'
            + dimensions[0]
            + b'" height="'
            + dimensions[1]
            + b'"/>',
            picture,
        )
        return re.sub(
            rb'<hp:imgDim\s+dimwidth="0"\s+dimheight="0"\s*/>',
            b'<hp:imgDim dimwidth="'
            + dimensions[0]
            + b'" dimheight="'
            + dimensions[1]
            + b'"/>',
            picture,
        )

    return PICTURE_RE.sub(patch_picture, section_xml)


def _replace_attribute(tag: bytes, name: str, value: int) -> bytes:
    encoded_name = re.escape(name.encode("ascii"))
    pattern = rb'(\b' + encoded_name + rb'=")[^"]*(")'
    replacement = rb"\g<1>" + str(value).encode("ascii") + rb"\g<2>"
    patched, count = re.subn(pattern, replacement, tag, count=1)
    if count != 1:
        raise ValueError(f"missing {name} attribute in exported page geometry")
    return patched


def _patch_page_geometry(
    section_xml: bytes,
    page_def: Mapping[str, int | bool],
) -> bytes:
    def patch_page_pr(match: re.Match[bytes]) -> bytes:
        page_pr = match.group(0)
        opening_end = page_pr.find(b">") + 1
        opening = page_pr[:opening_end]
        body = page_pr[opening_end:]
        for xml_name, source_name in (("width", "width"), ("height", "height")):
            opening = _replace_attribute(opening, xml_name, int(page_def[source_name]))

        margin_match = re.search(rb"<hp:margin\b[^>]*/>", body)
        if margin_match is None:
            raise ValueError("missing margin element in exported page geometry")
        margin = margin_match.group(0)
        for xml_name, source_name in (
            ("left", "marginLeft"),
            ("right", "marginRight"),
            ("top", "marginTop"),
            ("bottom", "marginBottom"),
            ("header", "marginHeader"),
            ("footer", "marginFooter"),
            ("gutter", "marginGutter"),
        ):
            margin = _replace_attribute(margin, xml_name, int(page_def[source_name]))
        body = body[: margin_match.start()] + margin + body[margin_match.end() :]
        return opening + body

    patched, count = PAGE_PR_RE.subn(patch_page_pr, section_xml)
    if count != 1:
        raise ValueError("expected exactly one pagePr element per section")
    return patched


def normalize_exported_hwpx(
    source_file: BinaryIO,
    destination_path: Path,
    page_defs: Sequence[Mapping[str, int | bool]] | None = None,
) -> None:
    _ = source_file.seek(0)
    with zipfile.ZipFile(source_file, "r") as source:
        entries: list[tuple[zipfile.ZipInfo, bytes]] = []
        sections: list[bytes] = []
        names = set(source.namelist())
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "Contents/content.hpf":
                data = _mark_embedded_images(data)
            if item.filename.startswith("Contents/") and item.filename.endswith(".xml"):
                data = WHITESPACE_TABLE_PREFIX_RE.sub(b"", data)
                section_match = SECTION_NAME_RE.match(item.filename)
                if section_match:
                    section_index = int(section_match.group(1))
                    if page_defs is not None:
                        try:
                            page_def = page_defs[section_index]
                        except IndexError as error:
                            raise ValueError(
                                f"missing source page geometry for section {section_index}"
                            ) from error
                        data = _patch_page_geometry(data, page_def)
                    data = _patch_picture_sizes(data)
                    if b"xmlns:hwpunitchar=" not in data:
                        data = re.sub(
                            rb"<hs:sec\b",
                            (
                                b'<hs:sec xmlns:hwpunitchar="http://www.hancom.co.kr/'
                                b'hwpml/2016/HwpUnitChar"'
                            ),
                            data,
                            count=1,
                        )
                    sections.append(data)
            entries.append((item, data))

    preview = _preview_text(sections)
    patched_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".patched",
            delete=False,
        ) as patched_file:
            patched_path = Path(patched_file.name)
            with zipfile.ZipFile(
                patched_file, "w", zipfile.ZIP_DEFLATED
            ) as destination:
                for item, data in entries:
                    if item.filename == "Preview/PrvText.txt":
                        data = preview
                    compression = (
                        zipfile.ZIP_STORED
                        if item.filename == "mimetype"
                        else item.compress_type
                    )
                    destination.writestr(item, data, compress_type=compression)
                if "Preview/PrvText.txt" not in names:
                    destination.writestr("Preview/PrvText.txt", preview)
            patched_file.flush()
            _ = os.fsync(patched_file.fileno())
        os.replace(patched_path, destination_path)
    finally:
        if patched_path is not None and patched_path.exists():
            patched_path.unlink()
