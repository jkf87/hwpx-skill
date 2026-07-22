#!/usr/bin/env python3
"""Convert K-Teacher student-worksheet HTML into a styled HWPX document.

Pipeline:
    HTML -> normalized design-plan XML -> OWPML section0.xml -> HWPX

The converter intentionally supports a small, deterministic HTML vocabulary. It
does not attempt browser layout emulation; semantic components are mapped to
stable HWPX tables and paragraphs so the output remains editable and printable.
"""

from __future__ import annotations

import argparse
import copy
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree, html

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from hwpx_helpers import (  # noqa: E402
    NS_DECL,
    extract_secpr_and_colpr,
    make_empty_line,
    make_first_para,
    make_page_break,
    next_id,
    reset_id,
    xml_escape,
)

HH_NS = "http://www.hancom.co.kr/hwpml/2011/head"
HC_NS = "http://www.hancom.co.kr/hwpml/2011/core"
HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NS = {"hh": HH_NS, "hc": HC_NS, "hp": HP_NS}

# A4 width minus the K-Teacher HTML renderer's 18 mm left/right padding.
PAGE_WIDTH = 49323
KTEACHER_PALETTE = {
    "paper": "#FFFFFF",
    "ink": "#243447",
    "navy": "#19324D",
    "teal": "#128277",
    "cobalt": "#3267D6",
    "coral": "#E86A5A",
    "amber": "#A65E15",
    "line": "#D4DEE9",
    "soft": "#F7F9FC",
    "band": "#EDF3FA",
    "source": "#F0F8F6",
    "exit": "#FFF4E8",
    "exit_line": "#F0D0A9",
    "muted": "#627386",
    "answer_line": "#7C8DA0",
    "white": "#FFFFFF",
}


@dataclass(frozen=True)
class ThemeStyles:
    fills: dict[str, str]
    body: str
    body_bold: str
    title: str
    subtitle: str
    section: str
    caption: str
    muted: str
    task: str
    exit_label: str
    white: str
    white_small: str
    accent: str
    step_label: str


def _class_tokens(element: etree._Element) -> set[str]:
    return set((element.get("class") or "").split())


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _element_text(element: etree._Element) -> str:
    return _clean_text(" ".join(element.itertext()))


def _direct_text(element: etree._Element, skip: etree._Element | None = None) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if child is not skip:
            parts.append(child.text_content())
        if child.tail:
            parts.append(child.tail)
    return _clean_text(" ".join(parts))


def parse_html(input_path: Path) -> dict:
    """Parse K-Teacher worksheet HTML into a serializable design plan."""
    root = html.fromstring(input_path.read_text(encoding="utf-8"))
    body = root.find("body") if root.tag.lower() == "html" else root
    if body is None:
        body = root

    plan: dict = {"title": "K-Teacher 활동지", "blocks": []}

    def emit(element: etree._Element) -> None:
        tag = etree.QName(element).localname.lower()
        classes = _class_tokens(element)
        block_type = _clean_text(element.get("data-block-type")).replace("-", "_")

        if "doc-header" in classes:
            h1 = element.xpath(".//h1[1]")
            subtitle = element.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' subtitle ')][1]"
            )
            eyebrow = element.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' eyebrow ')][1]"
            )
            badge = element.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' header-badge ')][1]"
            )
            title = _clean_text(h1[0].text_content()) if h1 else _clean_text(element.text_content())
            sub = _clean_text(subtitle[0].text_content()) if subtitle else ""
            plan["title"] = title or plan["title"]
            plan["blocks"].append({
                "type": "doc_header",
                "title": title,
                "subtitle": sub,
                "eyebrow": _clean_text(eyebrow[0].text_content()) if eyebrow else "WORKSHEET",
                "badge": _clean_text(badge[0].text_content()) if badge else "WORKSHEET",
            })
            return

        if "page-run" in classes:
            plan["blocks"].append({"type": "page_run", "text": _element_text(element)})
            return

        if "footer" in classes:
            footer = {"type": "footer", "text": _element_text(element)}
            if plan["blocks"] and plan["blocks"][-1]["type"] == "page_break":
                plan["blocks"].insert(len(plan["blocks"]) - 1, footer)
            else:
                plan["blocks"].append(footer)
            return

        if "identity" in classes:
            labels: list[str] = []
            values: list[str] = []
            for item in element.xpath("./div"):
                label = item.xpath("./b[1]")
                label_el = label[0] if label else None
                labels.append(_clean_text(label_el.text_content()) if label_el is not None else "")
                values.append(_direct_text(item, label_el))
            if values:
                plan["blocks"].append({
                    "type": "identity",
                    "rows": [labels, values],
                    "header_rows": [False, False],
                    "row_heights_mm": [4.5, 6.5],
                })
            return

        if "goal-card" in classes:
            label = element.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' label ')][1]"
            )
            copy_node = element.xpath(".//p[1]")
            plan["blocks"].append({
                "type": "goal_card",
                "title": _clean_text(label[0].text_content()) if label else "MISSION",
                "text": _clean_text(copy_node[0].text_content()) if copy_node else _clean_text(element.text_content()),
            })
            return

        if "caption" in classes:
            plan["blocks"].append({"type": "caption", "text": _element_text(element)})
            return

        if "support" in classes or "submit-note" in classes:
            plan["blocks"].append({"type": "student_note", "text": _element_text(element)})
            return

        if block_type == "page_break" or "page-break" in classes:
            plan["blocks"].append({"type": "page_break"})
            return

        if tag == "section" and "block" in classes:
            for child in element:
                if isinstance(child.tag, str):
                    emit(child)
            return

        if tag in {"h1", "h2"}:
            text = _clean_text(element.text_content())
            if text:
                if tag == "h1" and plan["title"] == "K-Teacher 활동지":
                    plan["title"] = text
                plan["blocks"].append({"type": "section" if tag == "h2" else "heading", "text": text})
            return

        if tag == "h3":
            text = _clean_text(element.text_content())
            if text:
                plan["blocks"].append({"type": "subheading", "text": text})
            return

        if tag == "p":
            text = _clean_text(element.text_content())
            if text:
                known = {
                    "student_task", "student_note", "source_card", "sentence_support", "exit_ticket"
                }
                plan["blocks"].append({
                    "type": block_type if block_type in known else "paragraph",
                    "text": text,
                    **({"step": element.get("data-step")} if element.get("data-step") else {}),
                })
            return

        if tag in {"ul", "ol"}:
            ordered = tag == "ol"
            items = [_clean_text(li.text_content()) for li in element.xpath("./li")]
            plan["blocks"].append({"type": "list", "ordered": ordered, "items": [x for x in items if x]})
            return

        if tag == "table":
            rows: list[list[str]] = []
            header_rows: list[bool] = []
            row_heights: list[float] = []
            table_kind = block_type if block_type in {"answer_box", "fill_table", "data_table"} else "table"
            for row_index, tr in enumerate(element.xpath(".//tr")):
                cells = tr.xpath("./th | ./td")
                if not cells:
                    continue
                rows.append([_clean_text(cell.text_content()) for cell in cells])
                header_rows.append(
                    any(etree.QName(cell).localname.lower() == "th" for cell in cells)
                    or (table_kind in {"fill_table", "data_table"} and row_index == 0)
                )
                try:
                    row_heights.append(float(tr.get("data-row-height-mm") or "0"))
                except ValueError:
                    row_heights.append(0)
            if rows:
                caption = element.xpath("./caption[1]")
                plan["blocks"].append({
                    "type": table_kind,
                    "caption": _clean_text(caption[0].text_content()) if caption else "",
                    "rows": rows,
                    "header_rows": header_rows,
                    "row_heights_mm": row_heights,
                })
            return

        for child in element:
            if isinstance(child.tag, str):
                emit(child)

    pages = body.xpath("./section[contains(concat(' ', normalize-space(@class), ' '), ' page ')]")
    if not pages:
        pages = [body]
    for page_index, page in enumerate(pages):
        if page_index and (not plan["blocks"] or plan["blocks"][-1]["type"] != "page_break"):
            plan["blocks"].append({"type": "page_break"})
        for child in page:
            if isinstance(child.tag, str):
                emit(child)
    if not any(block["type"] == "doc_header" for block in plan["blocks"]):
        plan["blocks"].insert(0, {"type": "doc_header", "title": plan["title"], "subtitle": ""})
    return plan


def write_plan_xml(plan: dict, output_path: Path) -> None:
    root = etree.Element("kteacher-document", version="1")
    root.set("title", plan["title"])
    for block in plan["blocks"]:
        node = etree.SubElement(root, "block", type=block["type"])
        for key in ("title", "text", "subtitle", "caption", "eyebrow", "badge", "step"):
            if block.get(key):
                node.set(key, str(block[key]))
        if "lines" in block:
            node.set("lines", str(block["lines"]))
        if block["type"] == "list":
            node.set("ordered", "1" if block.get("ordered") else "0")
            for item in block["items"]:
                etree.SubElement(node, "item").text = item
        elif block["type"] in {"table", "fill_table", "data_table", "answer_box", "identity"}:
            heights = block.get("row_heights_mm", [0] * len(block["rows"]))
            for values, is_header, height_mm in zip(block["rows"], block["header_rows"], heights):
                row = etree.SubElement(
                    node, "row", header="1" if is_header else "0", height_mm=str(height_mm)
                )
                for value in values:
                    etree.SubElement(row, "cell").text = value
    output_path.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(root).write(
        str(output_path), encoding="UTF-8", xml_declaration=True, pretty_print=True
    )


def read_plan_xml(input_path: Path) -> dict:
    root = etree.parse(str(input_path)).getroot()
    plan = {"title": root.get("title") or "K-Teacher 문서", "blocks": []}
    for node in root.findall("block"):
        block: dict = {"type": node.get("type")}
        for key in ("title", "text", "subtitle", "caption", "eyebrow", "badge", "step"):
            if node.get(key):
                block[key] = node.get(key)
        if node.get("lines"):
            block["lines"] = int(node.get("lines"))
        if block["type"] == "list":
            block["ordered"] = node.get("ordered") == "1"
            block["items"] = [item.text or "" for item in node.findall("item")]
        elif block["type"] in {"table", "fill_table", "data_table", "answer_box", "identity"}:
            rows, header_rows, row_heights = [], [], []
            for row in node.findall("row"):
                rows.append([cell.text or "" for cell in row.findall("cell")])
                header_rows.append(row.get("header") == "1")
                row_heights.append(float(row.get("height_mm") or "0"))
            block["rows"] = rows
            block["header_rows"] = header_rows
            block["row_heights_mm"] = row_heights
        plan["blocks"].append(block)
    return plan


def _set_border_fill(
    element: etree._Element,
    face_color: str,
    borders: dict[str, tuple[str, str]] | None = None,
) -> None:
    element.set("threeD", "0")
    element.set("shadow", "0")
    borders = borders or {}
    for name in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
        border = element.find(f"hh:{name}", NS)
        if border is not None:
            color, width = borders.get(name, ("#FFFFFF", "0.1 mm"))
            border.set("type", "SOLID" if name in borders else "NONE")
            border.set("width", width)
            border.set("color", color)
    for fill in list(element):
        if etree.QName(fill).localname in {"fillBrush", "gradation", "imgBrush", "winBrush"}:
            element.remove(fill)
    # HWPX stores brushes in the core namespace. Hancom silently ignores a
    # syntactically valid hh:fillBrush, which leaves colored cards white.
    fill_brush = etree.SubElement(element, f"{{{HC_NS}}}fillBrush")
    etree.SubElement(
        fill_brush,
        f"{{{HC_NS}}}winBrush",
        faceColor=face_color,
        hatchColor="#000000",
        alpha="0",
    )


def _append_char_style(parent: etree._Element, source: etree._Element, *, text_color: str, height: int, bold: bool) -> str:
    clone = copy.deepcopy(source)
    new_id = str(max(int(el.get("id")) for el in parent if el.get("id")) + 1)
    clone.set("id", new_id)
    clone.set("textColor", text_color)
    clone.set("height", str(height))
    bold_el = clone.find("hh:bold", NS)
    if bold and bold_el is None:
        clone.append(etree.Element(f"{{{HH_NS}}}bold"))
    elif not bold and bold_el is not None:
        clone.remove(bold_el)
    parent.append(clone)
    parent.set("itemCnt", str(len(parent)))
    return new_id


def customize_header(source: Path, output: Path) -> ThemeStyles:
    """Append the K-Teacher worksheet palette and semantic styles."""
    tree = etree.parse(str(source))
    border_parent = tree.find(".//hh:borderFills", NS)
    char_parent = tree.find(".//hh:charProperties", NS)
    if border_parent is None or char_parent is None:
        raise ValueError("header.xml is missing borderFills or charProperties")
    border_source = border_parent.find("hh:borderFill[@id='4']", NS)
    # charPr 8 in the government template is Malgun Gothic. Reusing its font
    # references keeps the visual language modern and portable on school PCs.
    dark_source = char_parent.find("hh:charPr[@id='8']", NS)
    title_source = char_parent.find("hh:charPr[@id='8']", NS)
    hero_source = char_parent.find("hh:charPr[@id='8']", NS)
    if border_source is None or dark_source is None or title_source is None or hero_source is None:
        raise ValueError("government header does not contain required base styles")

    all_line = {
        name: (KTEACHER_PALETTE["line"], "0.2 mm")
        for name in ("leftBorder", "rightBorder", "topBorder", "bottomBorder")
    }
    style_specs = {
        "plain": (KTEACHER_PALETTE["paper"], {}),
        "header": (KTEACHER_PALETTE["paper"], {
            "bottomBorder": (KTEACHER_PALETTE["navy"], "0.8 mm")
        }),
        "header_badge": (KTEACHER_PALETTE["navy"], {}),
        "identity_label": (KTEACHER_PALETTE["soft"], all_line),
        "identity_value": (KTEACHER_PALETTE["paper"], all_line),
        "section_num": (KTEACHER_PALETTE["teal"], {}),
        "section_body": (KTEACHER_PALETTE["source"], {
            "bottomBorder": (KTEACHER_PALETTE["teal"], "0.2 mm")
        }),
        "goal_label": (KTEACHER_PALETTE["band"], all_line),
        "goal_body": (KTEACHER_PALETTE["band"], all_line),
        "task_step": (KTEACHER_PALETTE["cobalt"], all_line),
        "task_body": (KTEACHER_PALETTE["band"], all_line),
        "source": (KTEACHER_PALETTE["source"], {
            "leftBorder": (KTEACHER_PALETTE["teal"], "1.0 mm")
        }),
        "exit_rail": (KTEACHER_PALETTE["coral"], {}),
        "exit": (KTEACHER_PALETTE["exit"], {
            name: (KTEACHER_PALETTE["exit_line"], "0.2 mm")
            for name in ("leftBorder", "rightBorder", "topBorder", "bottomBorder")
        }),
        "table_header": (KTEACHER_PALETTE["navy"], all_line),
        "table_cell": (KTEACHER_PALETTE["paper"], all_line),
        "table_alt": (KTEACHER_PALETTE["soft"], all_line),
        "answer": (KTEACHER_PALETTE["paper"], {
            "bottomBorder": (KTEACHER_PALETTE["answer_line"], "0.2 mm")
        }),
        "page_run": (KTEACHER_PALETTE["paper"], {
            "bottomBorder": (KTEACHER_PALETTE["line"], "0.2 mm")
        }),
        "footer": (KTEACHER_PALETTE["paper"], {
            "topBorder": (KTEACHER_PALETTE["line"], "0.2 mm")
        }),
    }
    fills: dict[str, str] = {}
    next_fill = max(int(el.get("id")) for el in border_parent if el.get("id")) + 1
    for name, (color, borders) in style_specs.items():
        clone = copy.deepcopy(border_source)
        clone.set("id", str(next_fill))
        _set_border_fill(clone, color, borders)
        border_parent.append(clone)
        fills[name] = str(next_fill)
        next_fill += 1
    border_parent.set("itemCnt", str(len(border_parent)))

    body = _append_char_style(char_parent, dark_source, text_color=KTEACHER_PALETTE["ink"], height=1000, bold=False)
    body_bold = _append_char_style(char_parent, dark_source, text_color=KTEACHER_PALETTE["ink"], height=1000, bold=True)
    title = _append_char_style(char_parent, hero_source, text_color=KTEACHER_PALETTE["navy"], height=1900, bold=True)
    subtitle = _append_char_style(char_parent, dark_source, text_color=KTEACHER_PALETTE["muted"], height=900, bold=False)
    section = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["teal"], height=1050, bold=True)
    caption = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["navy"], height=950, bold=True)
    muted = _append_char_style(char_parent, dark_source, text_color=KTEACHER_PALETTE["muted"], height=900, bold=False)
    task = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["navy"], height=1000, bold=True)
    exit_label = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["amber"], height=850, bold=True)
    white = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["white"], height=1000, bold=True)
    white_small = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["white"], height=750, bold=True)
    accent = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["teal"], height=750, bold=True)
    step_label = _append_char_style(char_parent, title_source, text_color=KTEACHER_PALETTE["cobalt"], height=800, bold=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    return ThemeStyles(
        fills=fills,
        body=body,
        body_bold=body_bold,
        title=title,
        subtitle=subtitle,
        section=section,
        caption=caption,
        muted=muted,
        task=task,
        exit_label=exit_label,
        white=white,
        white_small=white_small,
        accent=accent,
        step_label=step_label,
    )


def _text_paragraph(text: str, char_style: str, *, para_style: str = "2") -> str:
    return (
        f'<hp:p id="{next_id()}" paraPrIDRef="{para_style}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{char_style}"><hp:t>{xml_escape(text)}</hp:t></hp:run></hp:p>'
    )


def _worksheet_secpr(secpr: str) -> str:
    """Align the inherited A4 section margins with K-Teacher's print layout."""
    wrapper = etree.fromstring(f"<root {NS_DECL}>{secpr}</root>".encode("utf-8"))
    root = wrapper[0]
    page = root.find("hp:pagePr", NS)
    margin = root.find("hp:pagePr/hp:margin", NS)
    if page is not None:
        page.set("width", "59528")
        page.set("height", "84186")
    if margin is not None:
        margin.set("left", "5102")
        margin.set("right", "5102")
        margin.set("top", "4535")
        margin.set("bottom", "4535")
        margin.set("header", "2835")
        margin.set("footer", "2835")
    return etree.tostring(root, encoding="unicode")


def _cell_xml(
    *,
    col: int,
    row: int,
    width: int,
    height: int,
    fill_style: str,
    paragraphs: list[str],
) -> str:
    return (
        f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="1" '
        f'borderFillIDRef="{fill_style}">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0">{"".join(paragraphs)}</hp:subList>'
        f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
        f'<hp:cellSz width="{width}" height="{height}"/>'
        f'<hp:cellMargin left="420" right="420" top="280" bottom="280"/></hp:tc>'
    )


def _table_wrapper(rows: list[str], *, row_count: int, col_count: int, height: int) -> str:
    return (
        f'<hp:p id="{next_id()}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0"><hp:tbl id="{next_id()}" zOrder="0" numberingType="TABLE" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'pageBreak="CELL" repeatHeader="0" rowCnt="{row_count}" colCnt="{col_count}" '
        f'cellSpacing="0" borderFillIDRef="4" noAdjust="0">'
        f'<hp:sz width="{PAGE_WIDTH}" widthRelTo="ABSOLUTE" height="{height}" '
        f'heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="170" bottom="170"/>'
        f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>{"".join(rows)}'
        f'</hp:tbl></hp:run></hp:p>'
    )


def _rounded_box(
    paragraphs: list[str],
    *,
    height: int,
    fill: str,
    line: str,
    rounding: int = 22,
    width: int = PAGE_WIDTH,
    margin_x: int = 760,
    margin_y: int = 380,
) -> str:
    """Return an inline, editable native HWPX rounded text box.

    HWPX table cells have no border-radius. OWPML rectangles do: ``hp:rect``
    uses the ``ratio`` attribute for corner curvature. Keeping the shape inline
    makes it participate in normal page flow instead of behaving like a loose
    floating decoration.
    """
    rounding = max(0, min(100, int(rounding)))
    p_id, shape_id, inst_id = next_id(), next_id(), next_id()
    cx, cy = width // 2, height // 2
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        f'columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
        f'<hp:rect id="{shape_id}" zOrder="0" numberingType="PICTURE" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{inst_id}" '
        f'ratio="{rounding}">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{width}" height="{height}"/>'
        f'<hp:curSz width="{width}" height="{height}"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="0"/>'
        f'<hp:renderingInfo>'
        f'<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'</hp:renderingInfo>'
        f'<hp:lineShape color="{line}" width="33" style="SOLID" endCap="FLAT" '
        f'headStyle="NORMAL" tailStyle="NORMAL" headfill="1" tailfill="1" '
        f'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="NORMAL" alpha="0"/>'
        f'<hc:fillBrush><hc:winBrush faceColor="{fill}" hatchColor="#000000" '
        f'alpha="0"/></hc:fillBrush>'
        f'<hp:drawText lastWidth="4294967295" name="" editable="0">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
        f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        f'{"".join(paragraphs)}</hp:subList>'
        f'<hp:textMargin left="{margin_x}" right="{margin_x}" top="{margin_y}" '
        f'bottom="{margin_y}"/></hp:drawText>'
        f'<hp:shadow type="NONE" color="#B2B2B2" offsetX="0" offsetY="0" alpha="0"/>'
        f'<hc:pt0 x="0" y="0"/><hc:pt1 x="{width}" y="0"/>'
        f'<hc:pt2 x="{width}" y="{height}"/><hc:pt3 x="0" y="{height}"/>'
        f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" '
        f'heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="170" bottom="170"/>'
        f'<hp:shapeComment>rounded worksheet card</hp:shapeComment>'
        f'</hp:rect><hp:t/></hp:run></hp:p>'
    )


def _mixed_paragraph(runs: list[tuple[str, str]], *, para_style: str = "2") -> str:
    body = "".join(
        f'<hp:run charPrIDRef="{style}"><hp:t>{xml_escape(text)}</hp:t></hp:run>'
        for text, style in runs if text
    )
    return (
        f'<hp:p id="{next_id()}" paraPrIDRef="{para_style}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">{body}</hp:p>'
    )


def _one_cell_box(
    paragraphs: list[str], fill_style: str, *, height: int = 2800
) -> str:
    cell = _cell_xml(
        col=0, row=0, width=PAGE_WIDTH, height=height,
        fill_style=fill_style, paragraphs=paragraphs,
    )
    return _table_wrapper([f"<hp:tr>{cell}</hp:tr>"], row_count=1, col_count=1, height=height)


def _doc_header(block: dict, plan_title: str, styles: ThemeStyles) -> str:
    title = block.get("title") or plan_title
    subtitle = block.get("subtitle", "")
    eyebrow = block.get("eyebrow", "SCIENCE · WORKSHEET")
    badge = re.sub(r"^WORKSHEET\s*", "", block.get("badge", ""), flags=re.I).strip()
    left_width = PAGE_WIDTH * 78 // 100
    right_width = PAGE_WIDTH - left_width
    left_paragraphs = [
        _text_paragraph(eyebrow, styles.accent),
        _text_paragraph(title, styles.title),
    ]
    if subtitle:
        left_paragraphs.append(_text_paragraph(subtitle, styles.subtitle))
    left = _cell_xml(
        col=0, row=0, width=left_width, height=6500,
        fill_style=styles.fills["header"], paragraphs=left_paragraphs,
    )
    right = _cell_xml(
        col=1, row=0, width=right_width, height=6500,
        fill_style=styles.fills["header_badge"],
        paragraphs=[
            _text_paragraph("WORKSHEET", styles.white_small),
            _text_paragraph(badge or "생각을 근거로", styles.white),
        ],
    )
    return _table_wrapper([f"<hp:tr>{left}{right}</hp:tr>"], row_count=1, col_count=2, height=6500)


def _identity(block: dict, styles: ThemeStyles) -> str:
    labels = block.get("rows", [[]])[0]
    values = block.get("rows", [[], []])[1] if len(block.get("rows", [])) > 1 else []
    percentages = [22, 15, 15, 28, 20]
    cells: list[str] = []
    used = 0
    for index, pct in enumerate(percentages):
        width = PAGE_WIDTH * pct // 100 if index < len(percentages) - 1 else PAGE_WIDTH - used
        used += width
        cells.append(_cell_xml(
            col=index, row=0, width=width, height=3400,
            fill_style=styles.fills["identity_value"],
            paragraphs=[
                _text_paragraph(labels[index] if index < len(labels) else "", styles.muted),
                _text_paragraph(values[index] if index < len(values) else " ", styles.body_bold),
            ],
        ))
    return _table_wrapper([f"<hp:tr>{''.join(cells)}</hp:tr>"], row_count=1, col_count=5, height=3400)


def _section_heading(number: int, text: str, styles: ThemeStyles) -> str:
    num_width = 3600
    body_width = PAGE_WIDTH - num_width
    left = _cell_xml(
        col=0, row=0, width=num_width, height=2600,
        fill_style=styles.fills["section_num"],
        paragraphs=[_text_paragraph(f"{number:02d}", styles.white_small)],
    )
    right = _cell_xml(
        col=1, row=0, width=body_width, height=2600,
        fill_style=styles.fills["section_body"],
        paragraphs=[_text_paragraph(text, styles.caption)],
    )
    return _table_wrapper([f"<hp:tr>{left}{right}</hp:tr>"], row_count=1, col_count=2, height=2600)


def _goal_card(title: str, text: str, styles: ThemeStyles) -> str:
    return _rounded_box(
        [
            _text_paragraph(title or "MISSION", styles.accent),
            _text_paragraph(text, styles.body_bold),
        ],
        height=3900,
        fill=KTEACHER_PALETTE["band"],
        line=KTEACHER_PALETTE["line"],
        rounding=24,
    )


def _student_task(text: str, step: str, styles: ThemeStyles) -> str:
    return _rounded_box(
        [
            _text_paragraph(f"STEP {step or '01'}", styles.step_label),
            _text_paragraph(text, styles.body_bold),
        ],
        height=max(4300, ((len(text) // 55) + 1) * 1500 + 2100),
        fill=KTEACHER_PALETTE["band"],
        line=KTEACHER_PALETTE["cobalt"],
        rounding=24,
    )


def _source_card(text: str, styles: ThemeStyles) -> str:
    height = max(3800, ((len(text) // 48) + 1) * 1050)
    return _rounded_box(
        [
            _text_paragraph("SOURCE NOTE", styles.accent),
            _text_paragraph(text, styles.body),
        ],
        height=height + 900,
        fill=KTEACHER_PALETTE["source"],
        line=KTEACHER_PALETTE["teal"],
        rounding=24,
    )


def _exit_ticket(text: str, styles: ThemeStyles) -> str:
    return _rounded_box(
        [
            _text_paragraph("EXIT TICKET · 가장 어려운 사례", styles.exit_label),
            _text_paragraph(text, styles.body_bold),
        ],
        height=4600,
        fill=KTEACHER_PALETTE["exit"],
        line=KTEACHER_PALETTE["coral"],
        rounding=24,
    )


def _answer_box(block: dict, styles: ThemeStyles) -> str:
    rows = block.get("rows") or [[""] for _ in range(4)]
    heights = block.get("row_heights_mm") or [8] * len(rows)
    row_xml: list[str] = []
    total_height = 0
    for row_index, values in enumerate(rows):
        height_mm = heights[row_index] if row_index < len(heights) else 8
        height = max(1800, int((height_mm or 8) * 283.465))
        total_height += height
        text = values[0] if values else ""
        cell = _cell_xml(
            col=0, row=row_index, width=PAGE_WIDTH, height=height,
            fill_style=styles.fills["answer"],
            paragraphs=[_text_paragraph(text or " ", styles.body)],
        )
        row_xml.append(f"<hp:tr>{cell}</hp:tr>")
    return _table_wrapper(row_xml, row_count=len(row_xml), col_count=1, height=total_height)


def _worksheet_table(block: dict, styles: ThemeStyles) -> str:
    rows = block["rows"]
    cols = max(len(row) for row in rows)
    row_xml: list[str] = []
    total_height = 0
    caption = block.get("caption", "")
    prefix = _text_paragraph(caption, styles.caption, para_style="4") if caption else ""
    first_header = rows[0] if rows else []
    if cols == 2 and first_header and first_header[0] in {"확인", "체크"}:
        widths = [PAGE_WIDTH * 15 // 100, PAGE_WIDTH * 85 // 100]
    else:
        widths = [PAGE_WIDTH // cols] * cols
        widths[-1] += PAGE_WIDTH - sum(widths)
    heights = block.get("row_heights_mm") or [0] * len(rows)
    for r_idx, values in enumerate(rows):
        height_mm = heights[r_idx] if r_idx < len(heights) else 0
        row_height = max(2300, int(height_mm * 283.465)) if height_mm else 2500
        total_height += row_height
        cells: list[str] = []
        is_header = block["header_rows"][r_idx]
        for c_idx in range(cols):
            value = values[c_idx] if c_idx < len(values) else ""
            if is_header:
                fill = styles.fills["table_header"]
                char = styles.white_small
            else:
                fill = styles.fills["table_alt"] if r_idx % 2 == 0 else styles.fills["table_cell"]
                char = styles.body
            cells.append(_cell_xml(
                col=c_idx, row=r_idx, width=widths[c_idx], height=row_height,
                fill_style=fill, paragraphs=[_text_paragraph(value or " ", char)],
            ))
        row_xml.append(f"<hp:tr>{''.join(cells)}</hp:tr>")
    return prefix + _table_wrapper(row_xml, row_count=len(rows), col_count=cols, height=total_height)


def _caption(text: str, styles: ThemeStyles) -> str:
    return _mixed_paragraph([("●  ", styles.accent), (text, styles.caption)], para_style="4")


def _page_run(text: str, styles: ThemeStyles) -> str:
    return _one_cell_box(
        [_text_paragraph(text, styles.muted)],
        styles.fills["page_run"], height=2200
    )


def _footer(text: str, styles: ThemeStyles) -> str:
    return _one_cell_box(
        [_text_paragraph(text, styles.muted)], styles.fills["footer"], height=2000
    )


def plan_to_section(plan_path: Path, section_path: Path, styles: ThemeStyles, reference_hwpx: Path) -> None:
    plan = read_plan_xml(plan_path)
    secpr, colpr = extract_secpr_and_colpr(reference_hwpx)
    if not secpr:
        raise ValueError(f"reference HWPX has no secPr: {reference_hwpx}")
    secpr = _worksheet_secpr(secpr)
    reset_id()
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>',
        f"<hs:sec {NS_DECL}>",
        make_first_para(secpr, colpr),
    ]
    section_number = 0
    task_number = 0
    for block in plan["blocks"]:
        kind = block["type"]
        if kind == "doc_header":
            parts.append(_doc_header(block, plan["title"], styles))
        elif kind == "identity":
            parts.append(_identity(block, styles))
        elif kind == "page_run":
            parts.append(_page_run(block.get("text", ""), styles))
        elif kind == "section":
            section_number += 1
            parts.append(_section_heading(section_number, block["text"], styles))
        elif kind == "goal_card":
            parts.append(_goal_card(block.get("title", "MISSION"), block.get("text", ""), styles))
        elif kind == "caption":
            parts.append(_caption(block.get("text", ""), styles))
        elif kind == "subheading":
            parts.append(_text_paragraph(block["text"], styles.caption, para_style="4"))
        elif kind == "student_task":
            task_number += 1
            parts.append(_student_task(
                block.get("text", ""), block.get("step") or f"{task_number:02d}", styles
            ))
        elif kind == "source_card":
            parts.append(_source_card(block.get("text", ""), styles))
        elif kind == "exit_ticket":
            parts.append(_exit_ticket(block.get("text", ""), styles))
        elif kind == "answer_box":
            parts.append(_answer_box(block, styles))
        elif kind == "paragraph":
            parts.append(_text_paragraph(block["text"], styles.body, para_style="4"))
        elif kind == "student_note":
            parts.append(_text_paragraph(block["text"], styles.body, para_style="4"))
        elif kind == "sentence_support":
            parts.append(_text_paragraph(block["text"], styles.muted, para_style="4"))
        elif kind == "heading":
            parts.append(_text_paragraph(block["text"], styles.title, para_style="4"))
        elif kind == "list":
            for index, item in enumerate(block["items"], start=1):
                marker = f"{index}." if block.get("ordered") else "•"
                parts.append(_text_paragraph(f"  {marker} {item}", styles.body, para_style="4"))
        elif kind in {"table", "fill_table", "data_table"}:
            parts.append(_worksheet_table(block, styles))
        elif kind == "footer":
            parts.append(_footer(block.get("text", ""), styles))
        elif kind == "page_break":
            parts.append(make_page_break())
    parts.extend([make_empty_line(), "</hs:sec>"])
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("\n".join(parts), encoding="utf-8")


def convert(
    input_html: Path,
    output_hwpx: Path,
    *,
    keep_xml: Path | None = None,
    creator: str = "",
) -> tuple[Path, Path]:
    output_hwpx.parent.mkdir(parents=True, exist_ok=True)
    source_header = SKILL_DIR / "templates" / "government" / "header.xml"
    reference_hwpx = SKILL_DIR / "assets" / "gyehoek-reference.hwpx"
    if not reference_hwpx.is_file():
        reference_hwpx = SKILL_DIR / "assets" / "report-template.hwpx"

    temp_context = tempfile.TemporaryDirectory(prefix="html2hwpx-") if keep_xml is None else None
    work = Path(temp_context.name) if temp_context else keep_xml
    assert work is not None
    work.mkdir(parents=True, exist_ok=True)
    plan_xml = work / "design-plan.xml"
    header_xml = work / "header.xml"
    section_xml = work / "section0.xml"

    plan = parse_html(input_html)
    write_plan_xml(plan, plan_xml)
    styles = customize_header(source_header, header_xml)
    plan_to_section(plan_xml, section_xml, styles, reference_hwpx)

    subprocess.run([
        sys.executable, str(SCRIPT_DIR / "build_hwpx.py"),
        "--header", str(header_xml), "--section", str(section_xml),
        "--title", plan["title"], "--creator", creator or "K-Teacher HWPX",
        "--output", str(output_hwpx),
    ], check=True)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "fix_namespaces.py"), str(output_hwpx)], check=True)
    subprocess.run([
        sys.executable, str(SCRIPT_DIR / "finalize_hwpx.py"), str(output_hwpx),
        "--strip-linesegarray", "--layout",
    ], check=True)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "validate.py"), str(output_hwpx), "--layout"], check=True)

    if temp_context:
        # Return logical paths for callers that only need the output; temporary XML
        # files are intentionally discarded unless --keep-xml was supplied.
        temp_context.cleanup()
    return plan_xml, section_xml


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert K-Teacher student-worksheet HTML to editable HWPX"
    )
    parser.add_argument("input", type=Path, help="Input HTML file")
    parser.add_argument("output", type=Path, help="Output .hwpx file")
    parser.add_argument("--keep-xml", type=Path, help="Keep design-plan.xml, header.xml, and section0.xml")
    parser.add_argument("--creator", default="", help="Document creator metadata")
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"HTML file not found: {args.input}")
    if args.output.suffix.lower() != ".hwpx":
        raise SystemExit("output must end with .hwpx")
    convert(args.input, args.output, keep_xml=args.keep_xml, creator=args.creator)
    print(f"CREATED: {args.output}")
    if args.keep_xml:
        print(f"XML: {args.keep_xml / 'design-plan.xml'}")
        print(f"OWPML: {args.keep_xml / 'section0.xml'}")


if __name__ == "__main__":
    main()
