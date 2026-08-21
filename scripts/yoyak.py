#!/usr/bin/env python3
"""요약보고(약식 보고) HWPX 생성기 — 마크다운 한 장 → 결재선 달린 보고서.

지자체·공공기관 실무부서가 단장·팀장 결재를 받아 올리는 1~3쪽짜리 보고다.
실제 부서 산출물 5건(2023~2025)을 실측해 형식을 익혔고, 문서 고유의 값
(기관명·인명·로고·전용 글꼴)은 모두 걷어냈다.

  ┌──────────────────────────────────────────────────────────┐
  │ [로고 또는 기관명]              ┌──────────────────────┐ │
  │                                │      보고일           │ │  ← 결재선 표
  │                                ├───────────┬──────────┤ │
  │                                │ 추진단장  │ 홍길동   │ │
  │                                │ 팀    장  │ 김철수   │ │
  │                                └───────────┴──────────┘ │
  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │  ← 제목 띠
  │                    보 고 서 제 목                        │
  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
  │ □ 소제목                                                 │
  │   ❍ 항목                                                 │
  │      - 세부                                              │
  │   ⇒ 결론                                                 │
  └──────────────────────────────────────────────────────────┘

형식의 세 층(어디까지 바꿔도 되나):
  기관이 정한 것   결재선 표 모양, 제목 띠 색, 글꼴 벌, 글머리 기호 체계.
                   기본값은 실측한 한 벌이다. 다른 부서면 이 파일의 상수를 바꾼다.
  내용이 정하는 것 결재선 칸 높이(인원수), 제목 폭(용지), 표 칸 너비(글자 수),
                   빈 줄 위치. 전부 계산한다 — 어떤 좌표도 박아 두지 않는다.
  글쓴이가 정하는 것 줄간격(기본 160%, 쪽에 맞출 때 140~170), 강조.

마크다운 문법:
    ---
    기관: ○○시 ○○추진단            로고 없을 때 왼쪽 위에 들어가는 글자
    로고: logo.png                   있으면 기관 대신 그림 (선택)
    보고일: 2026. 8. 21.(금)
    결재: 추진단장 홍길동(1234) / 팀장 김철수(1235)
    줄간격: 160                      선택
    ---
    # 보고서 제목
    리드문 — 첫 소제목 전의 일반 문단 (선택)
    ## 소제목                        → □ 소제목
    - 항목                           → ❍ 항목
      - 세부                         →    - 세부
    ⇒ 결론  (또는 => 결론)           → ⇒ 결론 (굵게)
    ※ 참고                           → ※ 참고 (작은 글자)
    | 구분 | A | B |                 → 표 (머리행 음영)
    ![설명](그림.png)                 → 그림 (본문 폭에 맞춤)

  강조: **굵게**  __밑줄__  ++파랑 굵게++  ==형광==   (한 번에 하나씩, 겹치지 않는다)
  표 칸 안: **굵게**, ++파랑++, !!빨강!!

사용법:
    python3 scripts/yoyak.py 보고.md -o 보고.hwpx
    python3 scripts/yoyak.py --sample -o 샘플.hwpx
"""
from __future__ import annotations

import argparse
import html
import re
import struct
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from hwpx_helpers import NS_DECL, next_id, reset_id, xml_escape  # noqa: E402

HEADER = SKILL_DIR / "templates" / "yoyak" / "header.xml"
BASE = SKILL_DIR / "templates" / "base"

# ── 스타일 ID (templates/yoyak/header.xml) ─────────────────────────────
CP_TITLE, CP_H1 = "19", "20"
CP_BODY, CP_BODY_B, CP_BODY_U, CP_BODY_BLUE, CP_BODY_HL = "21", "22", "23", "24", "25"
CP_CONCL, CP_NOTE, CP_APPR, CP_ORG = "26", "27", "28", "29"
CP_TBL, CP_TBL_B, CP_TBL_BLUE, CP_TBL_RED = "30", "31", "32", "33"

PP_HEAD, PP_TITLE, PP_DATE, PP_POS, PP_NAME = "29", "30", "31", "32", "33"
PP_H1, PP_ITEM, PP_SUB, PP_CONCL, PP_NOTE, PP_BODY = "34", "35", "36", "37", "38", "39"
PP_CELL, PP_PLAIN = "40", "41"
LS_PARAS = (PP_ITEM, PP_SUB, PP_CONCL, PP_NOTE, PP_BODY)   # 줄간격 조절 대상

BF_NONE, BF_APPR_TOP, BF_APPR_BOT, BF_TITLE_BAR, BF_TBL, BF_TBL_HEAD = "1", "7", "8", "9", "10", "11"

# ── 용지 (HWPUNIT, 1pt = 100) ───────────────────────────────────────────
PAGE_W, PAGE_H = 59528, 84188            # A4 세로
M_LEFT = M_RIGHT = 5102                  # 18mm — 실측 5건 중 4건
M_TOP, M_BOTTOM = 5669, 6519             # 20mm / 23mm
BODY_W = PAGE_W - M_LEFT - M_RIGHT       # 49324

# ── 결재선 표 ───────────────────────────────────────────────────────────
APPR_W = 20173                           # 71mm — 실측
APPR_DATE_H = 1382                       # 보고일 행
APPR_LINE_H = 1297                       # 결재자 한 명당 (실측 2명 2594)
APPR_COL_POS = 11501                     # 직위 칸
APPR_COL_NAME = APPR_W - APPR_COL_POS    # 성명 칸
CELL_M = (510, 510, 141, 141)            # 칸 여백 좌·우·상·하

# ── 제목 띠 ─────────────────────────────────────────────────────────────
TITLE_BAR_H = 383                        # 띠 두께 1.35mm
TITLE_ROW_H = 3092                       # 제목 행

# ── 로고 ────────────────────────────────────────────────────────────────
LOGO_H = 4156                            # 14.7mm — 결재선 표 높이에 맞춤

LINE_SPACING = 160

# ── 글머리 — 접두 공백은 문단 내어쓰기(intent)와 짝이다 ─────────────────
# ❍ 항목: "  ❍ " 폭 ≈ 36pt = PP_ITEM intent 3644. 공백을 빼면 둘째 줄이 안 맞는다.
PREFIX = {"h1": "□ ", "item": "  ❍ ", "sub": "     - ", "concl": "  ⇒ ", "note": "    ※ "}

CONTENT_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp"}


# ═══════════════════════════════════════════════════════════════════════
# 마크다운 파서
# ═══════════════════════════════════════════════════════════════════════
def parse_front_matter(text: str) -> tuple[dict, str]:
    """`--- … ---` 사이의 `키: 값`. 없으면 빈 dict."""
    m = re.match(r"\s*---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def parse_approvers(s: str) -> list[dict]:
    """'추진단장 홍길동(1234) / 팀장 김철수' → [{직위, 성명}, …]. 마지막 낱말이 성명."""
    out = []
    for part in s.split("/"):
        part = part.strip()
        if not part:
            continue
        bits = part.rsplit(" ", 1)
        if len(bits) == 2:
            out.append({"직위": bits[0].strip(), "성명": bits[1].strip()})
        else:
            out.append({"직위": part, "성명": ""})
    return out


TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")


def parse_body(text: str) -> tuple[str, list]:
    """본문을 블록 목록으로. 반환 (제목, 블록들).
    블록: ('h1', t) ('item', t) ('sub', t) ('concl', t) ('note', t) ('para', t)
          ('table', [[셀…]…]) ('image', alt, path)"""
    title = ""
    blocks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("# ") and not title:
            title = s[2:].strip()
        elif s.startswith("## "):
            blocks.append(("h1", s[3:].strip()))
        elif TABLE_ROW.match(line):
            rows = []
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                if not TABLE_SEP.match(lines[i]):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue
        elif IMAGE.match(line):
            m = IMAGE.match(line)
            blocks.append(("image", m.group(1), m.group(2)))
        elif re.match(r"^(⇒|=>)\s*", s):
            blocks.append(("concl", re.sub(r"^(⇒|=>)\s*", "", s)))
        elif s.startswith("※"):
            blocks.append(("note", s[1:].strip()))
        elif re.match(r"^[-*]\s+", s):
            indent = len(line) - len(line.lstrip())
            kind = "sub" if indent >= 2 else "item"
            blocks.append((kind, re.sub(r"^[-*]\s+", "", s)))
        else:
            blocks.append(("para", s))
        i += 1
    return title, blocks


# ═══════════════════════════════════════════════════════════════════════
# 런/문단 조립
# ═══════════════════════════════════════════════════════════════════════
EMPH = re.compile(r"(\*\*.+?\*\*|__.+?__|\+\+.+?\+\+|==.+?==|!!.+?!!)")


BODY_EMPH = {"**": CP_BODY_B, "__": CP_BODY_U, "++": CP_BODY_BLUE, "==": CP_BODY_HL, "!!": CP_BODY_BLUE}
TBL_EMPH = {"**": CP_TBL_B, "++": CP_TBL_BLUE, "!!": CP_TBL_RED, "__": CP_TBL_B, "==": CP_TBL_B}


def runs(text: str, base: str, table: bool = False, styles: dict | None = None) -> str:
    """강조 표식을 런으로 쪼갠다. 표 안이면 표용 charPr 를 쓴다.
    다른 서식(geomto 등)은 styles 로 자기 charPr 맵을 넘긴다."""
    style = styles if styles is not None else (TBL_EMPH if table else BODY_EMPH)
    out = []
    for piece in EMPH.split(text):
        if not piece:
            continue
        mark = piece[:2]
        if mark in style and piece.endswith(mark) and len(piece) > 4:
            out.append(f'<hp:run charPrIDRef="{style[mark]}"><hp:t>{xml_escape(piece[2:-2])}</hp:t></hp:run>')
        else:
            out.append(f'<hp:run charPrIDRef="{base}"><hp:t>{xml_escape(piece)}</hp:t></hp:run>')
    return "".join(out)


def para(pp: str, inner: str) -> str:
    return (f'<hp:p id="{next_id()}" paraPrIDRef="{pp}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">{inner}</hp:p>')


def text_para(pp: str, cp: str, text: str) -> str:
    if text == "":
        return para(pp, f'<hp:run charPrIDRef="{cp}"><hp:t/></hp:run>')
    return para(pp, runs(text, cp))


def spacer(cp: str = CP_BODY) -> str:
    return text_para(PP_PLAIN, cp, "")


# ── 표 ───────────────────────────────────────────────────────────────────
def cell(col: int, row: int, w: int, h: int, bf: str, paragraphs: list[str],
         valign: str = "CENTER", margin=CELL_M, colspan: int = 1, rowspan: int = 1) -> str:
    ml, mr, mt, mb = margin
    return (f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="0" '
            f'borderFillIDRef="{bf}"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
            f'vertAlign="{valign}" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" '
            f'textHeight="0" hasTextRef="0" hasNumRef="0">{"".join(paragraphs)}</hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
            f'<hp:cellSpan colSpan="{colspan}" rowSpan="{rowspan}"/>'
            f'<hp:cellSz width="{w}" height="{h}"/>'
            f'<hp:cellMargin left="{ml}" right="{mr}" top="{mt}" bottom="{mb}"/></hp:tc>')


def table(rows_xml: list[str], n_rows: int, n_cols: int, width: int, height: int,
          bf: str, inmargin=(510, 510, 141, 141), outmargin=(141, 141, 0, 0)) -> str:
    il, ir, it, ib = inmargin
    ol, or_, ot, ob = outmargin
    return (f'<hp:tbl id="{next_id()}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
            f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" '
            f'rowCnt="{n_rows}" colCnt="{n_cols}" cellSpacing="0" borderFillIDRef="{bf}" noAdjust="0">'
            f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" '
            f'vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="{ol}" right="{or_}" top="{ot}" bottom="{ob}"/>'
            f'<hp:inMargin left="{il}" right="{ir}" top="{it}" bottom="{ib}"/>'
            f'{"".join(rows_xml)}</hp:tbl>')


def approval_table(date: str, approvers: list[dict]) -> str:
    """결재선 표 — 보고일 한 행 + (직위 | 성명) 한 행. 결재자 수만큼 칸 높이가 는다."""
    n = max(1, len(approvers))
    body_h = APPR_LINE_H * n
    r0 = f'<hp:tr>{cell(0, 0, APPR_W, APPR_DATE_H, BF_APPR_TOP, [text_para(PP_DATE, CP_APPR, date)], colspan=2)}</hp:tr>'
    pos = [text_para(PP_POS, CP_APPR, a["직위"]) for a in approvers] or [text_para(PP_POS, CP_APPR, "")]
    names = [text_para(PP_NAME, CP_APPR, a["성명"]) for a in approvers] or [text_para(PP_NAME, CP_APPR, "")]
    r1 = (f'<hp:tr>{cell(0, 1, APPR_COL_POS, body_h, BF_APPR_BOT, pos)}'
          f'{cell(1, 1, APPR_COL_NAME, body_h, BF_APPR_BOT, names)}</hp:tr>')
    return table([r0, r1], 2, 2, APPR_W, APPR_DATE_H + body_h, BF_APPR_BOT,
                 outmargin=(141, 141, 141, 141))


def title_box(title: str) -> str:
    """제목 — 위아래 색 띠 사이에 가운데 정렬. 폭은 본문 폭."""
    w = BODY_W
    bar = lambda r: f'<hp:tr>{cell(0, r, w, TITLE_BAR_H, BF_TITLE_BAR, [text_para(PP_TITLE, CP_BODY, "")], margin=(0, 0, 0, 0))}</hp:tr>'
    mid = f'<hp:tr>{cell(0, 1, w, TITLE_ROW_H, BF_NONE, [text_para(PP_TITLE, CP_TITLE, title)])}</hp:tr>'
    tbl = table([bar(0), mid, bar(2)], 3, 1, w, TITLE_BAR_H * 2 + TITLE_ROW_H, BF_NONE,
                outmargin=(0, 0, 0, 0))
    return para(PP_TITLE, f'<hp:run charPrIDRef="{CP_BODY}">{tbl}</hp:run>')


def text_width(text: str, char_h: int) -> int:
    korean = sum(1 for c in text if ord(c) > 0x7F)
    return round(korean * char_h + (len(text) - korean) * char_h * 0.5)


def content_table(rows: list[list[str]]) -> str:
    """마크다운 표 → 머리행 음영 표. 칸 너비는 글자 수 비례, 합계는 본문 폭."""
    n_cols = max(len(r) for r in rows)
    rows = [r + [""] * (n_cols - len(r)) for r in rows]
    ch = 1300
    need = [max(text_width(re.sub(r"[*+!_=]{2}", "", r[c]), ch) + CELL_M[0] + CELL_M[1]
                for r in rows) for c in range(n_cols)]
    need = [max(x, 3000) for x in need]
    scale = BODY_W / sum(need)
    widths = [round(x * scale) for x in need]
    widths[-1] += BODY_W - sum(widths)
    line_adv = round(ch * 1.1)
    trs, total_h = [], 0
    for ri, r in enumerate(rows):
        n_lines = max(max(1, -(-text_width(re.sub(r"[*+!_=]{2}", "", r[c]), ch) //
                               max(1, widths[c] - CELL_M[0] - CELL_M[1]))) for c in range(n_cols))
        h = CELL_M[2] + CELL_M[3] + line_adv * n_lines + 400
        bf = BF_TBL_HEAD if ri == 0 else BF_TBL
        cp = CP_TBL_B if ri == 0 else CP_TBL
        tcs = [cell(c, ri, widths[c], h, bf, [para(PP_CELL, runs(r[c], cp, table=True))])
               for c in range(n_cols)]
        trs.append(f'<hp:tr>{"".join(tcs)}</hp:tr>')
        total_h += h
    tbl = table(trs, len(rows), n_cols, BODY_W, total_h, BF_TBL)
    return para(PP_PLAIN, f'<hp:run charPrIDRef="{CP_BODY}">{tbl}</hp:run>')


# ── 그림 ─────────────────────────────────────────────────────────────────
def image_px(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
    if data[:2] == b"BM":
        w, h = struct.unpack("<ii", data[18:26])
        return w, abs(h)
    return 400, 300


def pic(item_id: str, w: int, h: int, halign: str = "LEFT") -> str:
    cx, cy = w // 2, h // 2
    return (f'<hp:pic id="{next_id()}" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" '
            f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" '
            f'instid="{next_id()}" reverse="0"><hp:offset x="0" y="0"/>'
            f'<hp:orgSz width="{w}" height="{h}"/><hp:curSz width="{w}" height="{h}"/>'
            f'<hp:flip horizontal="0" vertical="0"/>'
            f'<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="0"/>'
            f'<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            f'<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
            f'<hc:img binaryItemIDRef="{item_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
            f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/><hc:pt2 x="{w}" y="{h}"/>'
            f'<hc:pt3 x="0" y="{h}"/></hp:imgRect>'
            f'<hp:imgClip left="0" right="{w}" top="0" bottom="{h}"/>'
            f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:imgDim dimwidth="{w}" dimheight="{h}"/><hp:effects/>'
            f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            f'horzAlign="{halign}" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/></hp:pic>')


# ═══════════════════════════════════════════════════════════════════════
# 본문 조립
# ═══════════════════════════════════════════════════════════════════════
def sec_pr(left: int = M_LEFT, right: int = M_RIGHT, top: int = M_TOP, bottom: int = M_BOTTOM,
           header: int = 0, footer: int = 0) -> str:
    return (f'<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" '
            f'tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="0" '
            f'textVerticalWidthHead="0" masterPageCnt="0">'
            f'<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0" strictXMLGrid="0"/>'
            f'<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
            f'<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" '
            f'border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" '
            f'showLineNumber="0"/><hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
            f'<hp:pagePr landscape="WIDELY" width="{PAGE_W}" height="{PAGE_H}" gutterType="LEFT_ONLY">'
            f'<hp:margin header="{header}" footer="{footer}" gutter="0" left="{left}" right="{right}" '
            f'top="{top}" bottom="{bottom}"/></hp:pagePr>'
            f'<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            f'<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
            f'<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
            f'<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
            f'<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            f'<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
            f'<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
            f'<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
            f'<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" headerInside="0" '
            f'footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
            f'<hp:pageBorderFill type="EVEN" borderFillIDRef="1" textBorder="PAPER" headerInside="0" '
            f'footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
            f'<hp:pageBorderFill type="ODD" borderFillIDRef="1" textBorder="PAPER" headerInside="0" '
            f'footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
            f'</hp:secPr>')


def col_pr() -> str:
    return ('<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" '
            'sameGap="0"/></hp:ctrl>')


def build_section(meta: dict, title: str, blocks: list, images: list) -> str:
    """images: 호출자가 채울 목록 [(item_id, Path)]. 여기서 로고·본문 그림을 등록한다."""
    reset_id(1000000000)
    P = ['<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>', f'<hs:sec {NS_DECL}>']

    # ── 머리: [로고|기관명] … [결재선] 을 한 문단에, 양끝 정렬(DISTRIBUTE)로 벌린다 ──
    approvers = parse_approvers(meta.get("결재", ""))
    logo = meta.get("로고", "").strip()
    if logo:
        lp = Path(logo)
        if not lp.is_absolute():
            lp = (meta.get("_base", Path(".")) / lp)
        if not lp.exists():
            raise SystemExit(f"로고 파일이 없다: {lp}")
        pw, ph = image_px(lp)
        w = round(LOGO_H * pw / ph)
        iid = f"image{len(images) + 1}"
        images.append((iid, lp))
        left = pic(iid, w, LOGO_H)
    else:
        left = f'<hp:t>{xml_escape(meta.get("기관", ""))}</hp:t>'
    head_run = (f'<hp:run charPrIDRef="{CP_ORG}">{left}<hp:t> </hp:t>'
                f'{approval_table(meta.get("보고일", ""), approvers)}<hp:t/></hp:run>')
    P.append(f'<hp:p id="{next_id()}" paraPrIDRef="{PP_HEAD}" styleIDRef="0" pageBreak="0" '
             f'columnBreak="0" merged="0"><hp:run charPrIDRef="{CP_BODY}">{sec_pr()}{col_pr()}</hp:run>'
             f'{head_run}</hp:p>')

    # ── 제목 띠 ──
    P.append(title_box(title))
    P.append(spacer())

    # ── 본문 ──
    first_h1 = True
    for b in blocks:
        t = b[0]
        if t == "h1":
            if not first_h1:
                P.append(spacer())
            first_h1 = False
            P.append(text_para(PP_H1, CP_H1, PREFIX["h1"] + b[1]))
        elif t == "item":
            P.append(text_para(PP_ITEM, CP_BODY, PREFIX["item"] + b[1]))
        elif t == "sub":
            P.append(text_para(PP_SUB, CP_BODY, PREFIX["sub"] + b[1]))
        elif t == "concl":
            P.append(text_para(PP_CONCL, CP_CONCL, PREFIX["concl"] + b[1]))
        elif t == "note":
            P.append(text_para(PP_NOTE, CP_NOTE, PREFIX["note"] + b[1]))
        elif t == "para":
            P.append(text_para(PP_BODY, CP_BODY, b[1]))
        elif t == "table":
            P.append(content_table(b[1]))
        elif t == "image":
            ip = Path(b[2])
            if not ip.is_absolute():
                ip = meta.get("_base", Path(".")) / ip
            if not ip.exists():
                raise SystemExit(f"그림 파일이 없다: {ip}")
            pw, ph = image_px(ip)
            w = min(BODY_W, pw * 75)            # 96dpi 기준 1px = 75 HWPUNIT
            h = round(w * ph / pw)
            iid = f"image{len(images) + 1}"
            images.append((iid, ip))
            P.append(para(PP_PLAIN, f'<hp:run charPrIDRef="{CP_BODY}">{pic(iid, w, h, "CENTER")}<hp:t/></hp:run>'))
    P.append("</hs:sec>")
    return "\n".join(P)


def patched_header(ls: int, header: Path = None, base_ls: int = None, paras: tuple = None) -> str:
    """본문 줄간격을 바꾼 header.xml. 기본값이면 원본 그대로."""
    header = header or HEADER
    base_ls = base_ls or LINE_SPACING
    paras = paras or LS_PARAS
    x = header.read_text(encoding="utf-8")
    if ls == base_ls:
        return x
    for pid in paras:
        m = re.search(r'<hh:paraPr id="%s".*?</hh:paraPr>' % pid, x, re.S)
        fixed = m.group(0).replace(f'<hh:lineSpacing type="PERCENT" value="{base_ls}"',
                                   f'<hh:lineSpacing type="PERCENT" value="{ls}"')
        assert fixed != m.group(0), f"paraPr {pid} 줄간격 자리를 못 찾았다"
        x = x[:m.start()] + fixed + x[m.end():]
    return x


# ═══════════════════════════════════════════════════════════════════════
# 패키징 — 모든 엔트리 1980-01-01, 날짜 메타는 보고일. 같은 입력 → 같은 바이트.
# ═══════════════════════════════════════════════════════════════════════
T_RE = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)


def write_hwpx(out: Path, header: str, section: str, title: str, images: list, date_text: str) -> None:
    texts = [html.unescape(re.sub(r"<[^>]+>", "", t)) for t in T_RE.findall(section)]
    prv = "\n".join(t for t in texts if t.strip())
    hpf = (BASE / "Contents" / "content.hpf").read_text(encoding="utf-8")
    hpf = hpf.replace("<opf:title/>", f"<opf:title>{xml_escape(title)}</opf:title>")
    iso = _iso_date(date_text)
    hpf = re.sub(r'(<opf:meta name="(?:CreatedDate|ModifiedDate)" content="text")/>', rf"\1>{iso}</opf:meta>", hpf)
    items = "".join(f'<opf:item id="{iid}" href="BinData/{iid}{p.suffix.lower()}" '
                    f'media-type="{CONTENT_MIME.get(p.suffix.lower(), "image/png")}" isEmbeded="1"/>'
                    for iid, p in images)
    hpf = hpf.replace("</opf:manifest>", items + "</opf:manifest>")

    entries = [("mimetype", (BASE / "mimetype").read_bytes()),
               ("version.xml", (BASE / "version.xml").read_bytes()),
               ("settings.xml", (BASE / "settings.xml").read_bytes()),
               ("META-INF/container.rdf", (BASE / "META-INF" / "container.rdf").read_bytes()),
               ("META-INF/container.xml", (BASE / "META-INF" / "container.xml").read_bytes()),
               ("META-INF/manifest.xml", (BASE / "META-INF" / "manifest.xml").read_bytes()),
               ("Contents/content.hpf", hpf.encode("utf-8")),
               ("Contents/header.xml", header.encode("utf-8")),
               ("Contents/section0.xml", section.encode("utf-8")),
               ("Preview/PrvText.txt", prv.encode("utf-8")),
               ("Preview/PrvImage.png", (BASE / "Preview" / "PrvImage.png").read_bytes())]
    entries += [(f"BinData/{iid}{p.suffix.lower()}", p.read_bytes()) for iid, p in images]
    with zipfile.ZipFile(out, "w") as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def _iso_date(s: str) -> str:
    m = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", s or "")
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}T00:00:00Z"
    return "2000-01-01T00:00:00Z"


def generate(md_path: Path, out: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(text)
    meta["_base"] = md_path.parent
    title, blocks = parse_body(body)
    if not title:
        raise SystemExit("제목이 없다 — `# 제목` 줄을 넣어라")
    images: list = []
    section = build_section(meta, title, blocks, images)
    header = patched_header(int(meta.get("줄간격") or LINE_SPACING))
    write_hwpx(out, header, section, title, images, meta.get("보고일", ""))
    return out


SAMPLE = """---
기관: ○○시 ○○추진단
보고일: 2026. 8. 21.(금)
결재: 추진단장 홍길동(1234) / 정책팀장 김철수(1235)
---
# 「○○시 공공 AX 실습교육」 운영 현황 보고

## 추진 개요
- **(목적)** 실무자의 AI 활용 역량을 높여 ==실제 업무에 바로 적용==할 수 있게 한다
- **(대상)** 본청·직속기관 6급 이하 실무자 120명(4기수 × 30명)
- **(기간)** 2026. 9. ∼ 11.(3개월), 기수당 2일 과정

## 교육 과정 구성

| 구분 | 1일차 | 2일차 | 비고 |
|---|---|---|---|
| 주제 | 문서 자동화 | 데이터 분석 | 실습 중심 |
| 도구 | 한글·엑셀 + AI | 엑셀·BI + AI | ++개인 노트북 지참++ |
| 산출물 | 보고서 초안 1건 | 현황 대시보드 1건 | !!평가 반영!! |

## 문 제 점
- **(참여 편차)** 부서별 업무 부담으로 __실습 시간 확보가 어려운 부서__가 있음
  - 특히 민원 부서는 2일 연속 이탈이 사실상 불가
  - 온라인 병행 요구가 있으나 실습 품질 저하 우려
- **(사후 관리 부재)** 교육 후 현업 적용을 점검할 장치가 없음
⇒ 기수 구성을 **부서 순환형**으로 바꾸고, 교육 후 30일 적용 사례 1건 제출을 의무화

## 향후 계획
- 9월 1기 운영 후 만족도·적용 사례를 분석해 2기부터 과정 보완
  - 분석 결과는 10월 첫째 주 별도 보고
※ 강사진 구성과 예산 집행 계획은 붙임 자료 참조
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="요약보고 HWPX 생성기")
    ap.add_argument("input", nargs="?", help="마크다운 파일")
    ap.add_argument("-o", "--output", default="요약보고.hwpx")
    ap.add_argument("--sample", action="store_true", help="샘플 마크다운으로 생성")
    ap.add_argument("--emit-sample", action="store_true", help="샘플 마크다운을 표준출력으로")
    a = ap.parse_args()
    if a.emit_sample:
        sys.stdout.write(SAMPLE)
        return 0
    if a.sample:
        tmp = Path(a.output).with_suffix(".sample.md")
        tmp.write_text(SAMPLE, encoding="utf-8")
        generate(tmp, Path(a.output))
        tmp.unlink()
    elif a.input:
        generate(Path(a.input), Path(a.output))
    else:
        ap.error("입력 마크다운이 필요하다 (또는 --sample)")
    print(f"WROTE {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
