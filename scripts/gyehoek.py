#!/usr/bin/env python3
"""공공기관 계획서 기본문서 생성기 — 행안부 업무계획 레퍼런스 복제 방식.

assets/gyehoek-reference.hwpx(2025년 행정안전부 주요업무 추진계획)를 **복제**하여
표(23개)·이미지·글꼴·스타일을 100% 보존하고, 표지 제목·작성연월을 교체한다.
표지(제목 페이지)와 목차(순서) 포함 여부를 토글할 수 있다.

⚠️ 계획서 생성 전에는 **PreToolUse 훅(gyehoek_hook.py)** 이 제목/목차 포함 여부를
   사용자에게 먼저 물어보도록 강제한다. 따라서 호출 시 제목·목차 결정을 명시해야 한다:
     제목: --title "..."  또는  --no-title
     목차: --toc           또는  --no-toc

사용법:
    python3 scripts/gyehoek.py --title "2026년 ○○ 추진계획" --date "2026. 1." --toc  --output 계획서.hwpx
    python3 scripts/gyehoek.py --no-title --no-toc --output 계획서.hwpx
"""
import argparse
import html
import re
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REF = SKILL_DIR / "assets" / "gyehoek-reference.hwpx"

# 레퍼런스 고정 앵커 (행안부 업무계획)
COVER_TITLE = "2025년 주요업무 추진계획"
COVER_DATE = "2025. 1."
TOC_ANCHOR = "순   서"                                  # 목차(순서) 표
BODY_ANCHOR = "Ⅰ. 2025년 행정안전부 업무 추진 방향"      # 본문 첫 섹션


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _split_top_blocks(inner):
    """<hs:sec> 내부를 **최상위** <hp:p> 블록 리스트로 분리.

    표 셀 안의 중첩 <hp:p>를 깊이로 추적해 최상위 경계만 자른다(중첩 표/subList가
    깨지지 않도록). 블록 사이의 공백/기타 텍스트는 직전 블록에 흡수되지 않게 별도 보존."""
    blocks, pos, n = [], 0, len(inner)
    while pos < n:
        m = re.compile(r"<hp:p\b").search(inner, pos)
        if not m:
            blocks.append(("raw", inner[pos:]))
            break
        if m.start() > pos:
            blocks.append(("raw", inner[pos:m.start()]))
        depth, end = 0, None
        for t in re.finditer(r"<hp:p\b|</hp:p>", inner[m.start():]):
            depth += 1 if t.group().startswith("<hp:p") else -1
            if depth == 0:
                end = m.start() + t.end()
                break
        end = end or n
        blocks.append(("p", inner[m.start():end]))
        pos = end
    return blocks


def _text(block):
    return html.unescape(re.sub("<[^>]+>", "", block)).strip()


def build_section(x, title=None, date=None, include_title=True, include_toc=True):
    m = re.search(r"(<hs:sec\b[^>]*>)(.*)(</hs:sec>)", x, re.DOTALL)
    head, inner, tail = x[:m.start()] + m.group(1), m.group(2), m.group(3)
    blocks = _split_top_blocks(inner)

    # 블록 분류: secPr 문단(0) / 표지 / 목차(순서) / 본문
    toc_idx = body_idx = None
    for i, (kind, b) in enumerate(blocks):
        if kind != "p":
            continue
        t = _text(b)
        if toc_idx is None and t.startswith("순") and "Ⅰ" in t:
            toc_idx = i
        if body_idx is None and BODY_ANCHOR in t:
            body_idx = i
            break
    # secPr 문단 = 첫 'p' 블록
    sec_idx = next(i for i, (k, _) in enumerate(blocks) if k == "p")

    cover = blocks[sec_idx + 1:toc_idx] if toc_idx else blocks[sec_idx + 1:body_idx]
    toc = blocks[toc_idx:body_idx] if toc_idx else []
    body = blocks[body_idx:] if body_idx is not None else []

    kept = [blocks[sec_idx]]
    if include_title:
        kept += cover
    if include_toc:
        kept += toc
    kept += body

    new_inner = "".join(b for _, b in kept)
    section = head + new_inner + tail

    # 표지 제목/날짜 교체 (표지 유지 시)
    if include_title:
        if title:
            section = section.replace(_esc(COVER_TITLE), _esc(title), 1)
        if date:
            section = section.replace(_esc(COVER_DATE), _esc(date), 1)
    return section


def generate(output, title=None, date=None, include_title=True, include_toc=True):
    with zipfile.ZipFile(REF) as z:
        section = z.read("Contents/section0.xml").decode("utf-8")
    new_section = build_section(section, title, date, include_title, include_toc)
    texts = re.findall(r"<hp:t>(.*?)</hp:t>", new_section, re.DOTALL)
    prv = "\n".join(html.unescape(t) for t in texts if t.strip())
    with zipfile.ZipFile(REF) as zin, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "Contents/section0.xml":
                data = new_section.encode("utf-8")
            elif it.filename == "Preview/PrvText.txt":
                data = prv.encode("utf-8")
            if it.filename == "mimetype":
                zout.writestr(it, data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(it, data)
    return output


def main():
    ap = argparse.ArgumentParser(description="공공기관 계획서 생성기(행안부 업무계획 레퍼런스 복제)")
    gt = ap.add_mutually_exclusive_group()
    gt.add_argument("--title", help="표지 제목(지정 시 표지 포함)")
    gt.add_argument("--no-title", action="store_true", help="표지(제목 페이지) 제외")
    gc = ap.add_mutually_exclusive_group()
    gc.add_argument("--toc", action="store_true", help="목차(순서) 포함")
    gc.add_argument("--no-toc", action="store_true", help="목차(순서) 제외")
    ap.add_argument("--date", help="표지 작성연월 (예: 2026. 1.)")
    ap.add_argument("--output", default="계획서.hwpx")
    args = ap.parse_args()

    include_title = not args.no_title
    include_toc = args.toc and not args.no_toc
    out = generate(args.output, title=args.title, date=args.date,
                   include_title=include_title, include_toc=include_toc)
    print("WROTE", out)


if __name__ == "__main__":
    main()
