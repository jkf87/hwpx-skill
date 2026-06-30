#!/usr/bin/env python3
"""
마크다운 → HWPX 변환기 (md2hwpx)

마크다운 파일을 파싱하여 HWPX section0.xml을 생성하고,
build_hwpx.py를 통해 완성된 HWPX 문서를 만든다.

Usage:
    python md2hwpx.py input.md -o output.hwpx
    python md2hwpx.py input.md -o output.hwpx --template report
    python md2hwpx.py input.md -o output.hwpx --template report --header custom_header.xml
    python md2hwpx.py input.md -o output.hwpx --title "문서 제목" --creator "작성자"

마크다운 → HWPX 스타일 매핑 (report 템플릿 기준):
    # 제목       → charPrIDRef=7 (20pt 볼드), paraPrIDRef=20 (가운데)
    ## 섹션      → charPrIDRef=8 (14pt 볼드), paraPrIDRef=0
    ### 소제목   → charPrIDRef=13 (12pt 볼드 돋움), paraPrIDRef=27 (섹션헤더 테두리)
    #### 하위    → charPrIDRef=10 (10pt 볼드+밑줄), paraPrIDRef=0
    본문         → charPrIDRef=0 (10pt 바탕), paraPrIDRef=0
    **볼드**     → charPrIDRef=9 (10pt 볼드)
    > 인용       → charPrIDRef=11 (9pt), paraPrIDRef=24 (들여쓰기)
    - 목록       → charPrIDRef=0, paraPrIDRef=24 (들여쓰기)
      - 하위목록 → charPrIDRef=0, paraPrIDRef=25 (깊은 들여쓰기)
    1. 번호목록  → charPrIDRef=0, paraPrIDRef=24
"""
from __future__ import annotations  # Python 3.9 호환: list[...] | None 등 어노테이션 지연 평가

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

# ─── 스타일 매핑 프로파일 ───────────────────────────────────────

STYLE_PROFILES = {
    "report": {
        "title":        {"charPr": "7",  "paraPr": "20"},  # 20pt 볼드, 가운데
        "h2":           {"charPr": "8",  "paraPr": "0"},   # 14pt 볼드
        "h3":           {"charPr": "13", "paraPr": "27"},  # 12pt 볼드 돋움, 섹션헤더
        "h4":           {"charPr": "10", "paraPr": "0"},   # 10pt 볼드+밑줄
        "body":         {"charPr": "0",  "paraPr": "0"},   # 10pt 바탕
        "bold":         {"charPr": "9"},                    # 10pt 볼드
        "small":        {"charPr": "11", "paraPr": "0"},   # 9pt
        "quote":        {"charPr": "11", "paraPr": "24"},  # 9pt, 들여쓰기
        "list_l1":      {"charPr": "0",  "paraPr": "24"},  # 들여쓰기 1
        "list_l2":      {"charPr": "0",  "paraPr": "25"},  # 들여쓰기 2
        "list_l3":      {"charPr": "0",  "paraPr": "26"},  # 들여쓰기 3
        "table_header": {"charPr": "9",  "paraPr": "21"},  # 볼드, 표 가운데
        "table_cell":   {"charPr": "0",  "paraPr": "22"},  # 표 본문
    },
    "gonmun": {
        "title":        {"charPr": "7",  "paraPr": "20"},
        "h2":           {"charPr": "8",  "paraPr": "0"},
        "h3":           {"charPr": "10", "paraPr": "0"},
        "h4":           {"charPr": "0",  "paraPr": "0"},
        "body":         {"charPr": "0",  "paraPr": "0"},
        "bold":         {"charPr": "10"},
        "small":        {"charPr": "9",  "paraPr": "0"},
        "quote":        {"charPr": "9",  "paraPr": "0"},
        "list_l1":      {"charPr": "0",  "paraPr": "0"},
        "list_l2":      {"charPr": "0",  "paraPr": "0"},
        "list_l3":      {"charPr": "0",  "paraPr": "0"},
        "table_header": {"charPr": "10", "paraPr": "21"},
        "table_cell":   {"charPr": "0",  "paraPr": "22"},
    },
    "base": {
        "title":        {"charPr": "3",  "paraPr": "0"},
        "h2":           {"charPr": "3",  "paraPr": "0"},
        "h3":           {"charPr": "0",  "paraPr": "0"},
        "h4":           {"charPr": "0",  "paraPr": "0"},
        "body":         {"charPr": "0",  "paraPr": "0"},
        "bold":         {"charPr": "0"},
        "small":        {"charPr": "0",  "paraPr": "0"},
        "quote":        {"charPr": "0",  "paraPr": "0"},
        "list_l1":      {"charPr": "0",  "paraPr": "0"},
        "list_l2":      {"charPr": "0",  "paraPr": "0"},
        "list_l3":      {"charPr": "0",  "paraPr": "0"},
        "table_header": {"charPr": "0",  "paraPr": "0"},
        "table_cell":   {"charPr": "0",  "paraPr": "0"},
    },
}

# minutes와 proposal은 report와 유사한 매핑
STYLE_PROFILES["minutes"] = STYLE_PROFILES["report"].copy()
STYLE_PROFILES["proposal"] = STYLE_PROFILES["report"].copy()

# ─── secPr 템플릿 (첫 문단에 포함) ─────────────────────────────

SECPR_TEMPLATE = """<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0">
        <hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>
        <hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>
        <hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>
        <hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>
        <hp:pagePr landscape="WIDELY" width="59528" height="84186" gutterType="LEFT_ONLY">
          <hp:margin header="4252" footer="4252" gutter="0" left="8504" right="8504" top="5668" bottom="4252"/>
        </hp:pagePr>
        <hp:footNotePr>
          <hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>
          <hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>
          <hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>
          <hp:numbering type="CONTINUOUS" newNum="1"/>
          <hp:placement place="EACH_COLUMN" beneathText="0"/>
        </hp:footNotePr>
        <hp:endNotePr>
          <hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>
          <hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>
          <hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>
          <hp:numbering type="CONTINUOUS" newNum="1"/>
          <hp:placement place="END_OF_DOCUMENT" beneathText="0"/>
        </hp:endNotePr>
        <hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">
          <hp:offset left="1417" right="1417" top="1417" bottom="1417"/>
        </hp:pageBorderFill>
        <hp:pageBorderFill type="EVEN" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">
          <hp:offset left="1417" right="1417" top="1417" bottom="1417"/>
        </hp:pageBorderFill>
        <hp:pageBorderFill type="ODD" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">
          <hp:offset left="1417" right="1417" top="1417" bottom="1417"/>
        </hp:pageBorderFill>
      </hp:secPr>
      <hp:ctrl>
        <hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/>
      </hp:ctrl>"""


# ─── XML 생성 헬퍼 ──────────────────────────────────────────────

class SectionBuilder:
    """section0.xml을 구성하는 빌더."""

    def __init__(self, profile: dict):
        self.profile = profile
        self.paragraphs: list[str] = []
        self._next_id = 1000000001
        self._first_para = True

    def _get_id(self) -> str:
        pid = str(self._next_id)
        self._next_id += 1
        return pid

    def _make_para(self, text: str, style_key: str, runs: list[tuple[str, str]] | None = None) -> str:
        """단일 문단 XML 생성.

        Args:
            text: 본문 텍스트 (runs가 None일 때 사용)
            style_key: 스타일 프로파일 키
            runs: [(charPrIDRef, text), ...] 형태의 런 목록 (혼합 서식용)
        """
        style = self.profile.get(style_key, self.profile["body"])
        char_pr = style["charPr"]
        para_pr = style.get("paraPr", "0")
        pid = self._get_id()

        # 첫 문단에는 secPr 포함
        if self._first_para:
            self._first_para = False
            return f'''  <hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    <hp:run charPrIDRef="{char_pr}">
      {SECPR_TEMPLATE}
    </hp:run>
    <hp:run charPrIDRef="{char_pr}">
      <hp:t>{xml_escape(text)}</hp:t>
    </hp:run>
  </hp:p>'''

        if runs:
            run_xml = "\n    ".join(
                f'<hp:run charPrIDRef="{cpr}"><hp:t>{xml_escape(t)}</hp:t></hp:run>'
                for cpr, t in runs
            )
            return f'''  <hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    {run_xml}
  </hp:p>'''

        return f'''  <hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    <hp:run charPrIDRef="{char_pr}">
      <hp:t>{xml_escape(text)}</hp:t>
    </hp:run>
  </hp:p>'''

    def add_empty_line(self):
        """빈 줄 추가."""
        pid = self._get_id()
        if self._first_para:
            self._first_para = False
            self.paragraphs.append(f'''  <hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    <hp:run charPrIDRef="0">
      {SECPR_TEMPLATE}
    </hp:run>
    <hp:run charPrIDRef="0"><hp:t/></hp:run>
  </hp:p>''')
        else:
            self.paragraphs.append(f'''  <hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    <hp:run charPrIDRef="0"><hp:t/></hp:run>
  </hp:p>''')

    def add_paragraph(self, text: str, style_key: str = "body"):
        """일반 문단 추가."""
        self.paragraphs.append(self._make_para(text, style_key))

    def add_mixed_paragraph(self, runs: list[tuple[str, str]], style_key: str = "body"):
        """혼합 서식 문단 추가. runs: [(style_key, text), ...]"""
        style = self.profile.get(style_key, self.profile["body"])
        para_pr = style.get("paraPr", "0")
        resolved_runs = []
        for sk, t in runs:
            s = self.profile.get(sk, self.profile["body"])
            resolved_runs.append((s["charPr"], t))
        pid = self._get_id()
        if self._first_para:
            self._first_para = False
            first_cpr = resolved_runs[0][0] if resolved_runs else "0"
            run_xml = "\n    ".join(
                f'<hp:run charPrIDRef="{cpr}"><hp:t>{xml_escape(t)}</hp:t></hp:run>'
                for cpr, t in resolved_runs
            )
            self.paragraphs.append(f'''  <hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    <hp:run charPrIDRef="{first_cpr}">
      {SECPR_TEMPLATE}
    </hp:run>
    {run_xml}
  </hp:p>''')
        else:
            run_xml = "\n    ".join(
                f'<hp:run charPrIDRef="{cpr}"><hp:t>{xml_escape(t)}</hp:t></hp:run>'
                for cpr, t in resolved_runs
            )
            self.paragraphs.append(f'''  <hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
    {run_xml}
  </hp:p>''')

    def add_table(self, headers: list[str], rows: list[list[str]]):
        """표 추가."""
        num_cols = len(headers)
        num_rows = 1 + len(rows)  # 헤더 + 데이터
        body_width = 42520
        col_width = body_width // num_cols
        remainder = body_width - (col_width * num_cols)
        col_widths = [col_width] * num_cols
        col_widths[-1] += remainder  # 마지막 열에 나머지 배분
        row_height = 2400

        # 표를 담는 문단
        pid = self._get_id()
        tbl_id = self._get_id()

        total_height = row_height * num_rows

        def make_cell(text: str, is_header: bool, col_idx: int, row_idx: int) -> str:
            bf = "4" if is_header else "3"
            cp = self.profile.get("table_header" if is_header else "table_cell", self.profile["body"])
            char_pr = cp["charPr"]
            para_pr = cp.get("paraPr", "0")
            cell_pid = self._get_id()
            return f'''        <hp:tc name="" header="{1 if is_header else 0}" hasMargin="0" protect="0" editable="0" dirty="1" borderFillIDRef="{bf}">
          <hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">
            <hp:p paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0" id="{cell_pid}">
              <hp:run charPrIDRef="{char_pr}"><hp:t>{xml_escape(text)}</hp:t></hp:run>
            </hp:p>
          </hp:subList>
          <hp:cellAddr colAddr="{col_idx}" rowAddr="{row_idx}"/>
          <hp:cellSpan colSpan="1" rowSpan="1"/>
          <hp:cellSz width="{col_widths[col_idx]}" height="{row_height}"/>
          <hp:cellMargin left="170" right="170" top="0" bottom="0"/>
        </hp:tc>'''

        # 헤더 행
        header_cells = "\n".join(make_cell(h, True, i, 0) for i, h in enumerate(headers))
        header_row = f"      <hp:tr>\n{header_cells}\n      </hp:tr>"

        # 데이터 행
        data_rows = []
        for ri, row in enumerate(rows):
            # 열 수 맞추기
            padded = row + [""] * (num_cols - len(row))
            cells = "\n".join(make_cell(padded[ci], False, ci, ri + 1) for ci in range(num_cols))
            data_rows.append(f"      <hp:tr>\n{cells}\n      </hp:tr>")

        all_rows = header_row + "\n" + "\n".join(data_rows)

        if self._first_para:
            self._first_para = False
            secpr_part = f"""    <hp:run charPrIDRef="0">
      {SECPR_TEMPLATE}
    </hp:run>"""
        else:
            secpr_part = ""

        tbl_xml = f'''  <hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">
{secpr_part}
    <hp:run charPrIDRef="0">
      <hp:tbl id="{tbl_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" rowCnt="{num_rows}" colCnt="{num_cols}" cellSpacing="0" borderFillIDRef="3" noAdjust="0">
        <hp:sz width="{body_width}" widthRelTo="ABSOLUTE" height="{total_height}" heightRelTo="ABSOLUTE" protect="0"/>
        <hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>
        <hp:outMargin left="0" right="0" top="0" bottom="0"/>
        <hp:inMargin left="0" right="0" top="0" bottom="0"/>
{all_rows}
      </hp:tbl>
    </hp:run>
  </hp:p>'''
        self.paragraphs.append(tbl_xml)

    def build_xml(self) -> str:
        """완성된 section0.xml 문자열 반환."""
        ns_decl = (
            'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
            'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
            'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
            'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
            'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" '
            'xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
            'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:opf="http://www.idpf.org/2007/opf/" '
            'xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
            'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" '
            'xmlns:epub="http://www.idpf.org/2007/ops" '
            'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
        )
        body = "\n".join(self.paragraphs)
        return f'''<?xml version='1.0' encoding='UTF-8'?>
<hs:sec {ns_decl}>
{body}
</hs:sec>'''


# ─── 마크다운 파서 ───────────────────────────────────────────────

def parse_inline_bold(text: str) -> list[tuple[str, str]]:
    """**볼드** 마크다운을 분리하여 [(style_key, text), ...] 반환."""
    parts = re.split(r'\*\*(.+?)\*\*', text)
    result = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 0:
            result.append(("body", part))
        else:
            result.append(("bold", part))
    return result


def strip_markdown_formatting(text: str) -> str:
    """인라인 마크다운 문법 제거 (볼드, 이탤릭, 링크 등)."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'[\1]', text)
    return text


def parse_markdown_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """마크다운 파이프 테이블 파싱. (headers, rows) 반환."""
    if len(lines) < 2:
        return [], []
    header_line = lines[0].strip().strip('|')
    headers = [h.strip() for h in header_line.split('|')]

    rows = []
    for line in lines[2:]:  # separator (lines[1]) 건너뛰기
        row_line = line.strip().strip('|')
        cells = [strip_markdown_formatting(c.strip()) for c in row_line.split('|')]
        rows.append(cells)

    return [strip_markdown_formatting(h) for h in headers], rows


def md_to_section(md_text: str, template: str = "report") -> tuple[str, str]:
    """마크다운 텍스트를 section0.xml로 변환.

    Returns:
        (section_xml, title) 튜플
    """
    profile = STYLE_PROFILES.get(template, STYLE_PROFILES["report"])
    builder = SectionBuilder(profile)
    lines = md_text.split('\n')
    title = ""
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 빈 줄
        if not stripped:
            i += 1
            continue

        # YAML frontmatter 건너뛰기
        if stripped == '---' and i == 0:
            i += 1
            while i < len(lines) and lines[i].strip() != '---':
                i += 1
            i += 1  # closing ---
            continue

        # 수평선 (---)
        if re.match(r'^-{3,}$', stripped) or re.match(r'^\*{3,}$', stripped):
            builder.add_empty_line()
            i += 1
            continue

        # 제목 (# ~ ####)
        heading_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = strip_markdown_formatting(heading_match.group(2))

            if level == 1:
                if not title:
                    title = heading_text
                builder.add_empty_line()
                builder.add_paragraph(heading_text, "title")
                builder.add_empty_line()
            elif level == 2:
                builder.add_empty_line()
                # 섹션 번호 매기기
                builder.add_paragraph(heading_text, "h2")
                builder.add_empty_line()
            elif level == 3:
                builder.add_paragraph(heading_text, "h3")
            elif level == 4:
                builder.add_paragraph(heading_text, "h4")
            i += 1
            continue

        # 인용 (>)
        if stripped.startswith('>'):
            quote_text = strip_markdown_formatting(stripped.lstrip('> '))
            builder.add_paragraph(quote_text, "quote")
            i += 1
            continue

        # 마크다운 테이블
        if '|' in stripped and i + 1 < len(lines) and re.match(r'^\|?\s*[-:]+', lines[i + 1].strip()):
            table_lines = [lines[i]]
            j = i + 1
            while j < len(lines) and '|' in lines[j] and lines[j].strip():
                table_lines.append(lines[j])
                j += 1
            headers, rows = parse_markdown_table(table_lines)
            if headers:
                builder.add_table(headers, rows)
            i = j
            continue

        # 목록 (- 또는 * 또는 숫자.)
        list_match = re.match(r'^(\s*)([-*]|\d+\.)\s+(.+)$', stripped)
        if list_match:
            indent = len(line) - len(line.lstrip())
            marker = list_match.group(2)
            content = strip_markdown_formatting(list_match.group(3))

            if indent >= 4:
                style = "list_l3"
                prefix = "    - "
            elif indent >= 2:
                style = "list_l2"
                prefix = "  - "
            else:
                style = "list_l1"
                if re.match(r'\d+\.', marker):
                    prefix = f"{marker} "
                else:
                    prefix = "- "

            builder.add_paragraph(f"{prefix}{content}", style)
            i += 1
            continue

        # 코드 블록 (``` 로 시작)
        if stripped.startswith('```'):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing ```
            # 코드 블록을 일반 텍스트 문단으로 변환
            if code_lines:
                for cl in code_lines:
                    builder.add_paragraph(cl if cl.strip() else " ", "small")
            continue

        # 이미지 참조 (![alt](url)) - 텍스트로 변환
        img_match = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', stripped)
        if img_match:
            alt = img_match.group(1) or "이미지"
            builder.add_paragraph(f"[{alt}]", "small")
            i += 1
            continue

        # 일반 본문
        clean_text = strip_markdown_formatting(stripped)
        if '**' in stripped:
            # 볼드 혼합 텍스트
            runs = parse_inline_bold(stripped)
            # strip markdown from each run
            runs = [(sk, strip_markdown_formatting(t)) for sk, t in runs]
            if len(runs) > 1:
                builder.add_mixed_paragraph(runs, "body")
            else:
                builder.add_paragraph(clean_text, "body")
        else:
            builder.add_paragraph(clean_text, "body")

        i += 1

    return builder.build_xml(), title


# ─── 메인 ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="마크다운 파일을 HWPX 문서로 변환"
    )
    parser.add_argument("input", type=Path, help="입력 마크다운 파일")
    parser.add_argument("--output", "-o", type=Path, required=True, help="출력 HWPX 파일")
    parser.add_argument("--template", "-t", default="report",
                        choices=["base", "gonmun", "report", "minutes", "proposal"],
                        help="문서 템플릿 (기본: report)")
    parser.add_argument("--header", type=Path, help="커스텀 header.xml (선택)")
    parser.add_argument("--title", help="문서 제목 (자동 감지 가능)")
    parser.add_argument("--creator", help="작성자")
    parser.add_argument("--fix-ns", action="store_true", default=True,
                        help="네임스페이스 후처리 실행 (기본: 활성)")
    parser.add_argument("--no-fix-ns", action="store_true",
                        help="네임스페이스 후처리 건너뛰기")
    parser.add_argument("--theme",
                        help="문서 테마(기본/남색/진녹/진회색) — 생성 후 제목색·표머리색 적용")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"ERROR: 입력 파일 없음: {args.input}", file=sys.stderr)
        sys.exit(1)

    # 1. 마크다운 읽기
    md_text = args.input.read_text(encoding="utf-8")
    print(f"입력: {args.input} ({len(md_text)} chars)")

    # 2. section0.xml 생성
    section_xml, auto_title = md_to_section(md_text, args.template)
    title = args.title or auto_title or args.input.stem

    # 3. 임시 section0.xml 저장
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8') as f:
        f.write(section_xml)
        section_path = Path(f.name)
    print(f"section0.xml 생성: {section_path}")

    # 4. build_hwpx.py 호출
    build_script = SCRIPT_DIR / "build_hwpx.py"
    cmd = [
        sys.executable, str(build_script),
        "--template", args.template,
        "--section", str(section_path),
        "--title", title,
        "--output", str(args.output),
    ]
    if args.creator:
        cmd.extend(["--creator", args.creator])
    if args.header:
        cmd.extend(["--header", str(args.header)])

    print(f"빌드: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"ERROR: build_hwpx.py 실패:\n{result.stderr}", file=sys.stderr)
        section_path.unlink(missing_ok=True)
        sys.exit(1)

    # 5. 네임스페이스 후처리
    if not args.no_fix_ns:
        fix_script = SCRIPT_DIR / "fix_namespaces.py"
        if fix_script.is_file():
            ns_result = subprocess.run(
                [sys.executable, str(fix_script), str(args.output)],
                capture_output=True, text=True
            )
            if ns_result.returncode == 0:
                print("네임스페이스 후처리 완료")
            else:
                print(f"WARNING: 네임스페이스 후처리 실패:\n{ns_result.stderr}", file=sys.stderr)
        else:
            print(f"WARNING: fix_namespaces.py 없음: {fix_script}", file=sys.stderr)

    # 5.5 테마 적용 (생성된 문서에 제목색·표머리색 in-place)
    if args.theme:
        try:
            sys.path.insert(0, str(SCRIPT_DIR))
            from fill_hwpx import set_theme_hwpx
            r = set_theme_hwpx(str(args.output), str(args.output), theme=args.theme)
            print(f"테마 '{args.theme}' 적용: 제목 {r['headings_recolored']}개, "
                  f"표머리 {r['header_cells_colored']}칸")
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: 테마 적용 실패: {e}", file=sys.stderr)

    # 6. 검증
    validate_script = SCRIPT_DIR / "validate.py"
    if validate_script.is_file():
        v_result = subprocess.run(
            [sys.executable, str(validate_script), str(args.output)],
            capture_output=True, text=True
        )
        print(v_result.stdout)

    # 7. 텍스트 추출 (요약)
    extract_script = SCRIPT_DIR / "text_extract.py"
    if extract_script.is_file():
        e_result = subprocess.run(
            [sys.executable, str(extract_script), str(args.output)],
            capture_output=True, text=True
        )
        extracted = e_result.stdout.strip()
        lines = extracted.split('\n')
        para_count = len([l for l in lines if l.strip()])
        print(f"문단 수: {para_count}")
        # 처음 5줄만 표시
        preview = '\n'.join(lines[:5])
        print(f"미리보기:\n{preview}\n...")

    # 정리
    section_path.unlink(missing_ok=True)
    print(f"\n완료: {args.output} ({args.output.stat().st_size / 1024:.1f}KB)")


if __name__ == "__main__":
    main()
