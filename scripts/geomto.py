#!/usr/bin/env python3
"""기본계획·검토보고(장 배너형) HWPX 생성기 — 마크다운 → 로마숫자 장 배너 보고서.

지자체·공공기관 실무부서의 다쪽 보고서다. 제목 아래 본문이 `Ⅰ 추진배경 →
Ⅱ 현황 → Ⅲ 세부계획 → Ⅳ 기대효과 → Ⅴ 행정사항` 처럼 **로마숫자 장 배너**로
나뉘고, 각 장은 `❍ 항목 → - 세부` 개조식이다. 요약보고(yoyak.py)와 달리
결재선 표가 본문 위에 없고, 대신 **표지**(문서정보·결재란·제목 띠·기관명)를
붙일 수 있다. 실제 부서 산출물 6건(2023~2025)을 실측해 형식을 익혔고, 문서
고유의 값(기관명·인명·로고·전용 글꼴·그림 브러시)은 모두 걷어냈다.

  ┌────────────────────────────────────────────────────────┐
  │                       (2026. 8. 21. ○○추진단 홍길동)  │  ← 작성자 줄 (선택)
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │  ← 연파랑 띠
  │              「○○」 도입 기본계획                       │  ← 22pt 남색
  │ ████████████████████████████████████████████████████ │  ← 남색 띠
  │ ┌──────────────────────────────────────────────────┐ │
  │ │  리드 요약 — 무엇을 왜 하고자 하는지 두세 줄        │ │  ← 리드 박스 (선택)
  │ └──────────────────────────────────────────────────┘ │
  │ ▐Ⅰ▌ 추진배경                                          │  ← 장 배너 (파랑 네모 + 밑줄)
  │ ─────────────────────────────────────────────────── │
  │   ❍ 항목                                              │
  │     - 세부                                            │
  │       ▸ 3단                                           │
  │     ⇒ 결론                                            │
  └────────────────────────────────────────────────────────┘

형식의 세 층:
  기관이 정한 것   제목 띠 두 색, 장 배너 모양·색, 글꼴 벌, 글머리, 표지 표 구성.
                   기본값은 실측 한 벌. 다른 부서면 이 파일 상수와 header.xml 을 바꾼다.
  내용이 정하는 것 장 번호(순서대로 Ⅰ Ⅱ Ⅲ…), 제목·배너 폭(용지), 표 칸 너비(글자 수),
                   표지 결재란 칸 수(결재자 수), 빈 줄.
  글쓴이가 정하는 것 줄간격(기본 170%), 강조, 표지 유무, 리드 박스 유무.

마크다운 문법:
    ---
    부제: - 반려동물 배변 수거와 1인 가구를 위한 -    선택 (제목 위 작은 줄)
    작성: 2026. 8. 21. ○○추진단 홍길동                 선택 (오른쪽 위 작은 줄)
    표지: true                                          선택 — 표지 한 장 앞에 붙임
    기관: ○○시 / 부서: ○○추진단                        표지 아래 기관명
    문서번호: ○○추진단-  / 보존기간: 5년 / 결재일자: 2026.  .  / 공개여부: 비공개(5)
    결재: 주무관 / 팀장 / 추진단장 / 부시장 / 시장      표지 결재란 직위 (칸 수 = 인원수)
    줄간격: 170                                         선택
    ---
    # 제목
    > 리드 요약 (제목 바로 아래, 테두리 박스)           선택
    ## 추진배경            → ▐Ⅰ▌ 추진배경   (번호는 순서대로 자동)
    - 항목                 → ❍
      - 세부               →    -
        - 3단              →      ▸
    ⇒ 결론                 → ⇒ (굵게)
    ※ 참고 / * 각주        → ※ · *  (14pt)
    > 본문 중간의 박스     → 테두리 박스 (여러 줄 가능)
    | 표 |                 → 표 (머리행 음영, 칸 너비는 글자 수 비례)
    ![설명](그림.png)
    ---                    → 쪽 나눔

  강조: **굵게** __밑줄__ ++파랑 굵게++ ==형광==   (표 안: **굵게** ++파랑++ !!빨강!!)

사용법:
    python3 scripts/geomto.py 계획.md -o 계획.hwpx
    python3 scripts/geomto.py --sample -o 샘플.hwpx
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from hwpx_helpers import NS_DECL, next_id, reset_id, xml_escape  # noqa: E402
import yoyak  # noqa: E402
from yoyak import (cell, table, para, text_para, runs, pic, image_px, text_width,  # noqa: E402
                   content_table, write_hwpx, parse_front_matter, parse_approvers,
                   CP_BODY, CP_BODY_B, CP_CONCL, CP_NOTE, CP_TBL, BF_NONE, BF_TBL,
                   PP_PLAIN, PP_CELL)

HEADER = SKILL_DIR / "templates" / "geomto" / "header.xml"

# ── 이 서식만의 스타일 ID (templates/geomto/header.xml = yoyak + 아래) ──────
CP_TITLE, CP_SUBTITLE, CP_AUTHOR = "34", "35", "36"
CP_NUMERAL, CP_CHAP, CP_SUBSUB = "37", "38", "39"
CP_COVER, CP_COVER_TITLE, CP_ORG_BIG, CP_ORG_SMALL, CP_COVER_SUB = "40", "41", "42", "43", "44"
PP_AUTHOR, PP_TITLE, PP_SUBTITLE, PP_NUMERAL, PP_CHAP, PP_LEAD = "42", "43", "44", "45", "46", "47"
PP_ITEM, PP_SUB, PP_SUBSUB, PP_CONCL, PP_NOTE, PP_BODY = "48", "49", "50", "51", "52", "53"
PP_COVER_CELL, PP_ORG, PP_COVER_VAL = "54", "55", "56"
LS_PARAS = (PP_ITEM, PP_SUB, PP_SUBSUB, PP_CONCL, PP_NOTE, PP_BODY)
BF_TITLE_TOP, BF_TITLE_BOT, BF_CHAP_NUM, BF_CHAP_TITLE, BF_COVER_LABEL = "12", "13", "14", "15", "16"

# ── 용지 — 실측 6건: 좌우 5669(20mm) 5/6, 위아래 2834~5668 ──────────────
PAGE_W, PAGE_H = 59528, 84188
M_LEFT = M_RIGHT = 5669
M_TOP = M_BOTTOM = 4252
M_HEAD = M_FOOT = 1417
BODY_W = PAGE_W - M_LEFT - M_RIGHT            # 48190

LINE_SPACING = 170

# ── 제목 띠 — 위 연파랑 / 아래 남색 (원본은 그라데이션; 단색으로 단순화) ──
TITLE_BAR_H = 282
TITLE_ROW_H = 3546
SUBTITLE_ROW_H = 2600

# ── 장 배너 — [번호 칸][틈][제목 칸(밑줄)] ────────────────────────────────
CHAP_H = 2432
CHAP_NUM_W = 2481
CHAP_GAP_W = 1303
CELL_M = (510, 510, 141, 141)
LEAD_M = (1417, 1417, 141, 141)               # 리드 박스는 좌우 5mm 여백

# ── 표지 문서정보·결재 표 (실측) ──────────────────────────────────────────
COVER_LABEL_W, COVER_VAL_W, COVER_GAP_W = 4232, 9060, 4532
COVER_SIGN_MIN_W = 5267                       # 결재자 한 칸 최소 폭 — 인원수로 나눠 채운다
COVER_STAMP_W = 1951                          # 세로 '결재' 칸
COVER_ROWS = (2138, 765, 1564, 2329, 2329)    # 문서번호 / (머리 하단) / (서명 상단) / 결재일자 / 공개여부

ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"
PREFIX = {"item": "  ❍ ", "sub": "    - ", "subsub": "      ▸ ", "concl": "    ⇒ ",
          "note": "      ※ ", "foot": "      * "}


# ═══════════════════════════════════════════════════════════════════════
# 파서 — yoyak 의 문법 + 장(##) · 인용 박스(>) · 3단 · 각주(*) · 쪽 나눔(---)
# ═══════════════════════════════════════════════════════════════════════
TABLE_ROW = yoyak.TABLE_ROW
TABLE_SEP = yoyak.TABLE_SEP
IMAGE = yoyak.IMAGE


def parse_body(text: str) -> tuple[str, list]:
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
            blocks.append(("chapter", s[3:].strip()))
        elif s == "---":
            blocks.append(("pagebreak",))
        elif s.startswith(">"):
            box = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                box.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(("box", box))
            continue
        elif TABLE_ROW.match(line):
            rows = []
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                if not TABLE_SEP.match(lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
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
        elif re.match(r"^\*\s+", s):
            blocks.append(("foot", re.sub(r"^\*\s+", "", s)))
        elif re.match(r"^-\s+", s):
            indent = len(line) - len(line.lstrip())
            kind = "subsub" if indent >= 4 else "sub" if indent >= 2 else "item"
            blocks.append((kind, re.sub(r"^-\s+", "", s)))
        else:
            blocks.append(("para", s))
        i += 1
    return title, blocks


# ═══════════════════════════════════════════════════════════════════════
# 조각
# ═══════════════════════════════════════════════════════════════════════
def spacer() -> str:
    return text_para(PP_PLAIN, CP_BODY, "")


def page_break() -> str:
    return (f'<hp:p id="{next_id()}" paraPrIDRef="{PP_PLAIN}" styleIDRef="0" pageBreak="1" '
            f'columnBreak="0" merged="0"><hp:run charPrIDRef="{CP_BODY}"><hp:t/></hp:run></hp:p>')


def title_box(title: str, subtitle: str = "", width: int = BODY_W,
              title_cp: str = CP_TITLE, sub_cp: str = CP_SUBTITLE) -> str:
    """제목 — 위 연파랑 띠, 아래 남색 띠. 부제가 있으면 제목 위 한 줄."""
    rows = [f'<hp:tr>{cell(0, 0, width, TITLE_BAR_H, BF_TITLE_TOP, [text_para(PP_TITLE, CP_BODY, "")], margin=(0, 0, 0, 0))}</hp:tr>']
    mid = []
    h = TITLE_ROW_H
    if subtitle:
        mid.append(text_para(PP_SUBTITLE, sub_cp, subtitle))
        h += SUBTITLE_ROW_H
    mid.append(text_para(PP_TITLE, title_cp, title))
    rows.append(f'<hp:tr>{cell(0, 1, width, h, BF_NONE, mid, margin=(283, 283, 0, 0))}</hp:tr>')
    rows.append(f'<hp:tr>{cell(0, 2, width, TITLE_BAR_H, BF_TITLE_BOT, [text_para(PP_TITLE, CP_BODY, "")], margin=(0, 0, 0, 0))}</hp:tr>')
    tbl = table(rows, 3, 1, width, TITLE_BAR_H * 2 + h, BF_NONE, outmargin=(0, 0, 0, 0))
    return para(PP_TITLE, f'<hp:run charPrIDRef="{CP_BODY}">{tbl}</hp:run>')


def chapter_banner(n: int, title: str) -> str:
    """▐Ⅰ▌ 장 제목 ─────  (번호 칸 파랑 채움 + 제목 칸 아래 굵은 선)"""
    numeral = ROMAN[n - 1] if 0 < n <= len(ROMAN) else str(n)
    w_title = BODY_W - CHAP_NUM_W - CHAP_GAP_W
    tcs = [cell(0, 0, CHAP_NUM_W, CHAP_H, BF_CHAP_NUM, [text_para(PP_NUMERAL, CP_NUMERAL, numeral)]),
           cell(1, 0, CHAP_GAP_W, CHAP_H, BF_NONE, [text_para(PP_NUMERAL, CP_BODY, "")]),
           cell(2, 0, w_title, CHAP_H, BF_CHAP_TITLE, [para(PP_CHAP, runs(title, CP_CHAP))])]
    tbl = table([f'<hp:tr>{"".join(tcs)}</hp:tr>'], 1, 3, BODY_W, CHAP_H, BF_NONE,
                outmargin=(141, 141, 0, 0))
    return para(PP_PLAIN, f'<hp:run charPrIDRef="{CP_BODY}">{tbl}</hp:run>')


def text_box(lines: list[str], width: int = BODY_W, margin=LEAD_M) -> str:
    """테두리 1x1 박스. 리드 요약과 본문 중간 박스에 같이 쓴다. 높이는 줄 수로."""
    inner_w = width - margin[0] - margin[1]
    n_lines = 0
    for ln in lines:
        plain = re.sub(r"[*+!_=]{2}", "", ln) or " "
        n_lines += max(1, -(-text_width(plain, 1500) // inner_w))
    h = margin[2] + margin[3] + round(1500 * 1.7) * n_lines + 900
    paras = [para(PP_LEAD, runs(ln, CP_BODY)) if ln else text_para(PP_LEAD, CP_BODY, "") for ln in lines]
    tbl = table([f'<hp:tr>{cell(0, 0, width, h, BF_TBL, paras, margin=margin)}</hp:tr>'],
                1, 1, width, h, BF_TBL, outmargin=(0, 0, 0, 0))
    return para(PP_PLAIN, f'<hp:run charPrIDRef="{CP_BODY}">{tbl}</hp:run>')


def cover_info_table(meta: dict, approvers: list[str]) -> str:
    """표지 — 왼쪽 문서정보(문서번호·보존기간·결재일자·공개여부), 오른쪽 결재란(직위 머리 + 서명 칸 + 세로 '결재').
    한 표다. 결재자 수만큼 서명 칸이 생기고, 칸 폭은 남은 폭을 나눈다."""
    n = max(1, len(approvers))
    sign_total = BODY_W - COVER_LABEL_W - COVER_VAL_W - COVER_GAP_W - COVER_STAMP_W
    sign_w = max(COVER_SIGN_MIN_W, sign_total // n)
    sign_ws = [sign_w] * n
    sign_ws[-1] += sign_total - sum(sign_ws) if sign_total >= sum(sign_ws) else 0
    r = COVER_ROWS
    L = lambda t: [text_para(PP_COVER_CELL, CP_COVER, t)]
    V = lambda t: [text_para(PP_COVER_VAL, CP_COVER, t)]
    C = lambda t: [text_para(PP_COVER_CELL, CP_COVER, t)]
    cm = (141, 141, 141, 141)
    # row 0: 문서번호 | 값 | 틈 | 직위 머리×n (rowspan 2) | 결재 (rowspan 5)
    row0 = [cell(0, 0, COVER_LABEL_W, r[0], BF_COVER_LABEL, L("문서번호"), margin=cm),
            cell(1, 0, COVER_VAL_W, r[0], BF_TBL, V(meta.get("문서번호", "")), margin=cm),
            cell(2, 0, COVER_GAP_W, r[0], BF_NONE, C(""), margin=cm)]
    col = 3
    for i, pos in enumerate(approvers or [""]):
        row0.append(cell(col + i, 0, sign_ws[i], r[0] + r[1], BF_COVER_LABEL, C(pos), margin=cm, rowspan=2))
    stamp_col = col + n
    stamp = [text_para(PP_COVER_CELL, CP_COVER, "결"), text_para(PP_COVER_CELL, CP_COVER, ""),
             text_para(PP_COVER_CELL, CP_COVER, ""), text_para(PP_COVER_CELL, CP_COVER, "재")]
    row0.append(cell(stamp_col, 0, COVER_STAMP_W, sum(r), BF_COVER_LABEL, stamp, margin=cm, rowspan=5))
    # row 1: 보존기간 (rowspan 2) | 값 | 틈
    row1 = [cell(0, 1, COVER_LABEL_W, r[1] + r[2], BF_COVER_LABEL, L("보존기간"), margin=cm, rowspan=2),
            cell(1, 1, COVER_VAL_W, r[1] + r[2], BF_TBL, C(meta.get("보존기간", "")), margin=cm, rowspan=2),
            cell(2, 1, COVER_GAP_W, r[1] + r[2], BF_NONE, C(""), margin=cm, rowspan=2)]
    # row 2: 서명 칸 ×n (rowspan 3)
    row2 = [cell(col + i, 2, sign_ws[i], r[2] + r[3] + r[4], BF_TBL, C(""), margin=cm, rowspan=3)
            for i in range(n)]
    row3 = [cell(0, 3, COVER_LABEL_W, r[3], BF_COVER_LABEL, L("결재일자"), margin=cm),
            cell(1, 3, COVER_VAL_W, r[3], BF_TBL, V(meta.get("결재일자", "")), margin=cm),
            cell(2, 3, COVER_GAP_W, r[3], BF_NONE, C(""), margin=cm)]
    row4 = [cell(0, 4, COVER_LABEL_W, r[4], BF_COVER_LABEL, L("공개여부"), margin=cm),
            cell(1, 4, COVER_VAL_W, r[4], BF_TBL, C(meta.get("공개여부", "")), margin=cm),
            cell(2, 4, COVER_GAP_W, r[4], BF_NONE, C(""), margin=cm)]
    rows = [f'<hp:tr>{"".join(x)}</hp:tr>' for x in (row0, row1, row2, row3, row4)]
    total_w = COVER_LABEL_W + COVER_VAL_W + COVER_GAP_W + sum(sign_ws) + COVER_STAMP_W
    tbl = table(rows, 5, stamp_col + 1, total_w, sum(r), BF_NONE, outmargin=(0, 0, 0, 0))
    return para(PP_PLAIN, f'<hp:run charPrIDRef="{CP_BODY}">{tbl}</hp:run>')


def cover_page(meta: dict, title: str, subtitle: str, lead: list | None) -> list[str]:
    P = []
    P.append(cover_info_table(meta, [x.strip() for x in meta.get("결재", "").split("/") if x.strip()]))
    P += [spacer(), spacer()]
    P.append(title_box(title, subtitle, title_cp=CP_COVER_TITLE, sub_cp=CP_COVER_SUB))
    P.append(spacer())
    if lead:
        P.append(text_box(lead))
    P += [spacer()] * 6
    org, dept = meta.get("기관", ""), meta.get("부서", "")
    if org:
        P.append(text_para(PP_ORG, CP_ORG_BIG, org))
    if dept:
        P.append(text_para(PP_ORG, CP_ORG_SMALL, dept))
    P.append(page_break())
    return P


# ═══════════════════════════════════════════════════════════════════════
# 본문
# ═══════════════════════════════════════════════════════════════════════
def build_section(meta: dict, title: str, blocks: list, images: list) -> str:
    reset_id(1000000000)
    P = ['<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>', f'<hs:sec {NS_DECL}>']
    subtitle = meta.get("부제", "").strip()
    author = meta.get("작성", "").strip()
    # 제목 바로 아래 인용 박스는 리드
    lead = None
    if blocks and blocks[0][0] == "box":
        lead = blocks[0][1]
        blocks = blocks[1:]

    first = (f'<hp:p id="{next_id()}" paraPrIDRef="{PP_AUTHOR if author else PP_PLAIN}" styleIDRef="0" '
             f'pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{CP_BODY}">'
             f'{yoyak.sec_pr(M_LEFT, M_RIGHT, M_TOP, M_BOTTOM, M_HEAD, M_FOOT)}{yoyak.col_pr()}</hp:run>')
    cover = str(meta.get("표지", "")).lower() in ("true", "1", "yes", "y", "예", "있음")
    if cover:
        # 표지 첫 문단이 페이지 설정을 품는다
        P.append(first + f'<hp:run charPrIDRef="{CP_BODY}"><hp:t/></hp:run></hp:p>')
        P += cover_page(meta, title, subtitle, lead)
        if author:
            P.append(text_para(PP_AUTHOR, CP_AUTHOR, f"({author})"))
    else:
        P.append(first + (f'<hp:run charPrIDRef="{CP_AUTHOR}"><hp:t>({xml_escape(author)})</hp:t></hp:run>'
                          if author else f'<hp:run charPrIDRef="{CP_BODY}"><hp:t/></hp:run>') + "</hp:p>")
    P.append(title_box(title, subtitle))
    P.append(spacer())
    if lead:
        P.append(text_box(lead))
        P.append(spacer())

    chap = 0
    for b in blocks:
        t = b[0]
        if t == "chapter":
            chap += 1
            if chap > 1:
                P.append(spacer())
            P.append(chapter_banner(chap, b[1]))
        elif t == "item":
            P.append(text_para(PP_ITEM, CP_BODY, PREFIX["item"] + b[1]))
        elif t == "sub":
            P.append(text_para(PP_SUB, CP_BODY, PREFIX["sub"] + b[1]))
        elif t == "subsub":
            P.append(text_para(PP_SUBSUB, CP_SUBSUB, PREFIX["subsub"] + b[1]))
        elif t == "concl":
            P.append(text_para(PP_CONCL, CP_CONCL, PREFIX["concl"] + b[1]))
        elif t == "note":
            P.append(text_para(PP_NOTE, CP_SUBSUB, PREFIX["note"] + b[1]))
        elif t == "foot":
            P.append(text_para(PP_NOTE, CP_SUBSUB, PREFIX["foot"] + b[1]))
        elif t == "para":
            P.append(text_para(PP_BODY, CP_BODY, b[1]))
        elif t == "box":
            P.append(text_box(b[1], width=BODY_W - 2 * CHAP_NUM_W, margin=CELL_M))
        elif t == "table":
            P.append(content_table(b[1]))
        elif t == "pagebreak":
            P.append(page_break())
        elif t == "image":
            ip = Path(b[2])
            if not ip.is_absolute():
                ip = meta.get("_base", Path(".")) / ip
            if not ip.exists():
                raise SystemExit(f"그림 파일이 없다: {ip}")
            pw, ph = image_px(ip)
            w = min(BODY_W, pw * 75)
            h = round(w * ph / pw)
            iid = f"image{len(images) + 1}"
            images.append((iid, ip))
            P.append(para(PP_PLAIN, f'<hp:run charPrIDRef="{CP_BODY}">{pic(iid, w, h, "CENTER")}<hp:t/></hp:run>'))
    P.append("</hs:sec>")
    return "\n".join(P)


def generate(md_path: Path, out: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(text)
    meta["_base"] = md_path.parent
    title, blocks = parse_body(body)
    if not title:
        raise SystemExit("제목이 없다 — `# 제목` 줄을 넣어라")
    images: list = []
    section = build_section(meta, title, blocks, images)
    header = yoyak.patched_header(int(meta.get("줄간격") or LINE_SPACING), HEADER, LINE_SPACING, LS_PARAS)
    write_hwpx(out, header, section, title, images, meta.get("작성", "") or meta.get("결재일자", ""))
    return out


SAMPLE = """---
부제: - 공공기관 실무자의 AI 활용 역량 강화를 위한 -
작성: 2026. 8. 21. ○○추진단 홍길동
표지: true
기관: ○○시
부서: ○○추진단
문서번호: ○○추진단-
보존기간: 5년
결재일자: 2026.  .  .
공개여부: 비공개(5)
결재: 주무관 / 팀장 / 추진단장 / 부시장 / 시장
---
# 「공공 AX 실습교육」 도입 기본계획
> 실무자가 AI 도구를 **직접 써 보며 결과물을 만드는 실습형 교육**을 도입하여,
> 부서 업무의 자동화·데이터 활용을 현장에서 바로 쓸 수 있게 하고자 함.

## 추진배경
- 생성형 AI 보급으로 문서·데이터 업무의 처리 방식이 바뀌고 있으나, 실무자 교육은 **강의식 소개**에 머물러 현업 적용률이 낮음
  - 2025년 직원 설문: 교육 이수자 중 업무에 적용한 비율 ==23%==
  - "무엇을 할 수 있는지는 알겠는데 내 업무에 어떻게 붙이는지 모르겠다"는 응답 다수
- 타 기관은 실습 중심 과정으로 전환 중
  - ○○광역시: 2일 실습 과정 운영, 적용률 61%
  - ○○공단: 부서 과제를 가져와 해결하는 '과제형' 교육
⇒ 강의가 아니라 **실습과 과제**로 짜야 현업에 남는다
* 관련: ○○추진단-1234(2026. 3. 2.) 「AI 업무혁신 추진계획」

## 사업개요
- **(과정명)** 「공공 AX 실습교육」
- **(대상)** 본청·직속기관 6급 이하 실무자 120명(4기수 × 30명)
- **(기간)** 2026. 9. ∼ 11., 기수당 2일(14시간)
- **(방식)** 부서 과제 지참 → 도구 실습 → 산출물 제출 → 30일 후 적용 사례 공유

| 구분 | 1일차 | 2일차 | 비고 |
|---|---|---|---|
| 주제 | 문서 자동화 | 데이터 분석 | 실습 중심 |
| 도구 | 한글·엑셀 + AI | 엑셀·BI + AI | ++개인 노트북 지참++ |
| 산출물 | 보고서 초안 1건 | 현황 대시보드 1건 | !!평가 반영!! |

## 세부추진계획
- **(교육 과정)** 기수별 2일, 오전 도구 실습 · 오후 과제 해결
  - 1일차: 한글 문서 자동화(서식 채우기·요약·교정), 엑셀 수식 생성
    - 과제 예: 월간 실적 보고 초안 자동 생성
  - 2일차: 데이터 정리·시각화, 간단한 대시보드 제작
- **(강사진)** 외부 전문강사 2명 + 내부 선도 직원 4명(기수별 보조)
- **(사후 관리)** 교육 30일 후 적용 사례 1건 제출, 우수 사례는 전 직원 공유
> 《교육 운영 원칙》
> ▸ 강의는 전체의 30% 이내, 나머지는 실습
> ▸ 과제는 참여자가 자기 부서 업무에서 가져온다
> ▸ 산출물은 실제 업무에 쓸 수 있는 형태로 제출

## 기대효과
- 실무자 120명이 **자기 업무에서 쓰는** AI 활용 사례 120건 확보
- 부서별 반복 업무의 자동화 착수, 보고서·통계 작업 시간 단축
※ 정량 목표(적용률 50% 이상)는 1기 운영 후 조정

## 행정사항
- **(예산)** 강사료·교재비 등 ○○백만 원(2026년 본예산 ○○ 사업)
- **(일정)** 8월 강사 섭외·교재 제작 → 9월 1기 운영 → 12월 결과 보고
- **(협조)** 각 부서는 기수별 참여자 1~2명 추천, 교육일 업무 조정
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="기본계획·검토보고(장 배너형) HWPX 생성기")
    ap.add_argument("input", nargs="?", help="마크다운 파일")
    ap.add_argument("-o", "--output", default="기본계획.hwpx")
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
