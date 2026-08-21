#!/usr/bin/env python3
"""문서 편집 규범 추출·조판기 (analyze / render).

왜 이 방식인가
--------------
기존 "복제 후 문자열 치환" 은 구조적으로 레이아웃이 깨진다. 원본의 paraPr 는
줄마다 그 줄의 텍스트 길이에 맞춰 손으로 조정돼 있어(실측: gyehoek-reference
는 paraPr 242개가 의미 속성 기준으로도 206종), 텍스트만 바꾸면 각 줄이 '옛
텍스트에 맞춰진 기하학' 을 그대로 물고 있게 된다.

그래서 이렇게 나눈다.
  · header.xml (글꼴·스타일·테두리 정의)  → 원본 것을 그대로 쓴다. 충실도 100%.
  · section0.xml (본문 구조)              → 내용에 맞춰 새로 조판한다.
줄배치 캐시(linesegarray)를 넣지 않으므로 한컴이 열 때 다시 계산한다. 따라서
내용이 길든 짧든 레이아웃이 어긋날 수 없다.

사용법
------
  doc_spec.py analyze <ref.hwpx> -o spec/        # 규범 추출
  doc_spec.py render  spec/ <content.md> -o out.hwpx
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

INNER_TAG = re.compile(r"<[^>]*>")
T_RE = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)

# 문서 계층 마커 — 행정 문서의 표준 8단계에서 실제로 쓰이는 것들
# 기관마다 같은 층위에 다른 기호를 쓴다. 'ㅇ'(한글 이응)과 '○'(흰 원)은
# 눈으로는 같아 보이지만 다른 문자다 — 한쪽만 알면 계층이 통째로 뭉개진다.
MARKERS = [
    ("h_square", ("□", "▪", "■")),
    ("bullet", ("ㅇ", "○", "◦", "●")),
    ("arrow", ("⇒", "→", "➡")),
    ("record", ("￭", "▣")),
    ("tri", ("▸", "▶", "►")),
    ("note_ref", ("※",)),
    ("dash", ("-", "–", "‐")),
    ("note", ("*", "＊")),
]

# 층위 이름 → 대표 기호(조판 시 기본값)
MARKER_MAIN = {name: alts[0] for name, alts in MARKERS}


def text_of(frag: str) -> str:
    return "".join(html.unescape(INNER_TAG.sub("", t)) for t in T_RE.findall(frag))


def iter_blocks(inner: str):
    """<hs:sec> 바로 아래 최상위 <hp:p> 블록을 순서대로 산출."""
    pos = 0
    while pos < len(inner):
        m = re.compile(r"<hp:p\b").search(inner, pos)
        if not m:
            break
        start = m.start()
        depth, end = 0, None
        for t in re.finditer(r"<hp:p\b|</hp:p>", inner[start:]):
            depth += 1 if t.group().startswith("<hp:p") else -1
            if depth == 0:
                end = start + t.end()
                break
        if end is None:
            break
        yield start, end, inner[start:end]
        pos = end


def classify(text: str) -> str:
    s = text.lstrip()
    for name, alts in MARKERS:
        if s.startswith(alts):
            return name
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*\.", s):
        return "chapter"
    if re.match(r"^\d+\s*\.", s):
        return "topic"
    return "plain"


def load_section(path: Path) -> tuple[str, str, str]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("Contents/section0.xml").decode("utf-8")
    m = re.search(r"(.*?<hs:sec\b[^>]*>)(.*)(</hs:sec>.*)", xml, re.S)
    return m.group(1), m.group(2), m.group(3)


# ─────────────────────────── analyze ───────────────────────────

def analyze(ref: Path, outdir: Path) -> dict:
    head, inner, tail = load_section(ref)
    tpl = outdir / "templates"
    tpl.mkdir(parents=True, exist_ok=True)

    spec: dict = {
        "source": ref.name,
        "levels": {},
        "banners": {},
        "blocks": {},
        "tables": {},
        "objects": {},
        "stats": {},
    }

    # 첫 문단(secPr 보유) = 페이지 설정. 통째로 보존한다.
    first = next(iter_blocks(inner))[2]
    # 첫 문단이 secPr 과 '원본 제목 배너' 를 함께 담고 있는 문서가 있다
    # (한컴은 제목 표를 첫 문단 안에 넣기도 한다). 그대로 두면 조판하는 모든
    # 문서에 원본 제목이 딸려 들어간다. 페이지 설정만 남기고 개체는 뗀다.
    if "<hp:secPr" in first and ("<hp:tbl" in first or "<hp:pic" in first):
        m = re.search(r'(<hp:p\b[^>]*>.*?<hp:secPr\b.*?</hp:secPr>.*?</hp:run>).*?(</hp:p>)', first, re.S)
        if m:
            first = m.group(1) + m.group(2)
    (tpl / "first_para.xml").write_text(first, encoding="utf-8")
    spec["page"] = {"template": "first_para.xml",
                    "has_secPr": "<hp:secPr" in first}

    # 본문 문단: 마커별 대표 서식(가장 흔한 paraPr/charPr 조합)
    per_level: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, str] = {}
    counts = Counter()
    banner_cands: list[tuple[int, str, str]] = []
    content_tables: list[tuple[int, str, str]] = []
    images: list[str] = []

    for _s, _e, frag in iter_blocks(inner):
        has_tbl = "<hp:tbl" in frag
        has_pic = "<hp:pic" in frag
        txt = text_of(frag).strip()
        if has_pic:
            images.append(frag)
            counts["image"] += 1
            continue
        if has_tbl:
            # 짧은 표 = 제목 배너, 긴 표 = 콘텐츠 블록
            (banner_cands if len(txt) <= 40 else content_tables).append(
                (len(txt), txt, frag))
            counts["table"] += 1
            continue
        if not txt:
            counts["empty"] += 1
            continue
        counts["para"] += 1
        kind = classify(txt)
        pp = re.search(r'paraPrIDRef="(\d+)"', frag)
        cp = re.search(r'charPrIDRef="(\d+)"', frag)
        per_level[kind][(pp.group(1) if pp else "0", cp.group(1) if cp else "0")] += 1
        samples.setdefault(kind, txt[:60])

    for kind, combos in per_level.items():
        (pp, cp), n = combos.most_common(1)[0]
        spec["levels"][kind] = {
            "marker": MARKER_MAIN.get(kind, ""),
            "paraPr": pp, "charPr": cp,
            "count": sum(combos.values()),
            "variants": len(combos),
            "example": samples.get(kind, ""),
        }

    # 제목 배너: 표 구조(행×열)로 종류를 나눈다
    for _ln, txt, frag in banner_cands:
        rc = re.search(r'rowCnt="(\d+)" colCnt="(\d+)"', frag)
        shape = f"{rc.group(1)}x{rc.group(2)}" if rc else "?"
        cells = len(re.findall(r"<hp:tc\b", frag))
        name = f"banner_{shape}_{cells}"
        if name not in spec["banners"]:
            fn = f"{name}.xml"
            (tpl / fn).write_text(frag, encoding="utf-8")
            spec["banners"][name] = {"template": fn, "shape": shape,
                                     "cells": cells, "example": txt}

    # 콘텐츠 표: 셀 수가 적으면 강조 박스, 많으면 데이터 표
    for _ln, txt, frag in sorted(content_tables, key=lambda r: r[0]):
        cells = len(re.findall(r"<hp:tc\b", frag))
        rc = re.search(r'rowCnt="(\d+)" colCnt="(\d+)"', frag)
        rows = int(rc.group(1)) if rc else 0
        cols = int(rc.group(2)) if rc else 0
        # 역할 구분: 셀이 적으면 단문 박스, 행이 적고 병합이 있으면 제목 박스,
        # 행이 여러 개면 데이터 표. 열 수만으로 묶으면 제목 박스와 일정표가
        # 같은 종류로 뭉쳐 표가 박스 서식으로 조판되는 사고가 난다(실측).
        if cells <= 3:
            key, bucket = "callout", spec["blocks"]
        elif rows <= 3:
            key, bucket = f"titled_box_{cols}col", spec["blocks"]
        else:
            key, bucket = f"table_{cols}col", spec["tables"]
        if key not in bucket:
            fn = f"{key}.xml"
            (tpl / fn).write_text(frag, encoding="utf-8")
            bucket[key] = {"template": fn, "rows": rows, "cols": cols,
                           "cells": cells, "example": txt[:80]}

    if images:
        (tpl / "image.xml").write_text(images[0], encoding="utf-8")
        spec["objects"]["image"] = {"template": "image.xml", "count": len(images)}

    # 본문 폭 = 가장 넓은 표의 폭. 배너 폭을 다시 계산할 때 상한으로 쓴다.
    widths = [int(m.group(1)) for m in
              re.finditer(r'<hp:sz width="(\d+)" widthRelTo="ABSOLUTE"', inner)]
    spec["body_width"] = max(widths) if widths else 0
    spec["stats"] = dict(counts)
    (outdir / "spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    # 원본을 스타일 공급원으로 함께 보관한다(header.xml 을 그대로 쓰기 위함)
    shutil.copy(ref, outdir / "base.hwpx")
    return spec


def cmd_analyze(a) -> int:
    spec = analyze(Path(a.ref), Path(a.out))
    print(f"규범 추출 완료 → {a.out}/spec.json")
    print(f"  블록 구성: {spec['stats']}")
    print(f"  본문 계층 {len(spec['levels'])}종:")
    for k, v in sorted(spec["levels"].items(), key=lambda kv: -kv[1]["count"]):
        print(f"    {k:<10} {v['marker']:<2} paraPr={v['paraPr']:>4} "
              f"charPr={v['charPr']:>4}  {v['count']}개  예: {v['example'][:36]}")
    print(f"  제목 배너 {len(spec['banners'])}종: {list(spec['banners'])}")
    print(f"  강조 블록 {len(spec['blocks'])}종 / 데이터 표 {len(spec['tables'])}종")
    print(f"  이미지 배치: {'있음' if spec['objects'] else '없음'}")
    return 0



# ─────────────────────────── 조판 유틸 ───────────────────────────

LINESEG = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray>", re.S)


def esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


EMPH = re.compile(r"\*\*(.+?)\*\*")


def build_char_heights(base: Path) -> dict:
    """charPr id → 글자 크기(HWPUNIT). 폭·줄수 어림에 쓴다."""
    from xml.etree import ElementTree as ET
    ns = {"hh": "http://www.hancom.co.kr/hwpml/2011/head"}
    with zipfile.ZipFile(base) as z:
        root = ET.fromstring(z.read("Contents/header.xml"))
    return {c.get("id"): int(c.get("height") or 1000)
            for c in root.findall(".//hh:charProperties/hh:charPr", ns)}


def first_char_height(frag: str, heights: dict, default: int = 1000) -> int:
    """조각의 첫 run 이 쓰는 글자 크기."""
    m = re.search(r'charPrIDRef="(\d+)"', frag)
    return heights.get(m.group(1), default) if m else default


def build_bold_map(base: Path) -> dict:
    """charPr → '같은 글꼴의 굵게 charPr' 대응표.

    한글은 마크다운 렌더러가 아니라, `**굵게**` 를 그대로 흘려보내면 별표가
    글자로 박힌다(실사용에서 10곳 발생). 그래서 굵게 짝을 찾아 런을 나눈다.
    정확히 같은 (글꼴,크기,색) 짝은 드물어(실측 11종) 단계적으로 폴백한다.
    """
    from xml.etree import ElementTree as ET
    ns = {"hh": "http://www.hancom.co.kr/hwpml/2011/head"}
    with zipfile.ZipFile(base) as z:
        root = ET.fromstring(z.read("Contents/header.xml"))
    chars = []
    for c in root.findall(".//hh:charProperties/hh:charPr", ns):
        fr = c.find("hh:fontRef", ns)
        chars.append({
            "id": c.get("id"),
            "font": fr.get("hangul") if fr is not None else "?",
            "h": c.get("height"),
            "color": c.get("textColor"),
            "bold": c.find("hh:bold", ns) is not None,
        })
    bolds = [c for c in chars if c["bold"]]
    out = {}
    for c in chars:
        if c["bold"]:
            out[c["id"]] = c["id"]
            continue
        for pred in (lambda b: b["font"] == c["font"] and b["h"] == c["h"]
                     and b["color"] == c["color"],
                     lambda b: b["font"] == c["font"] and b["h"] == c["h"],
                     lambda b: b["font"] == c["font"]):
            hit = next((b["id"] for b in bolds if pred(b)), None)
            if hit:
                out[c["id"]] = hit
                break
    return out


def emphasis_runs(text: str, char_id: str, bold_map: dict | None) -> str:
    """`**굵게**` 를 런으로 쪼갠다. 굵게 짝이 없으면 별표만 제거한다."""
    bold_id = (bold_map or {}).get(char_id)
    if "**" not in text:
        return f'<hp:run charPrIDRef="{char_id}"><hp:t>{esc(text)}</hp:t></hp:run>'
    if not bold_id:
        return (f'<hp:run charPrIDRef="{char_id}">'
                f"<hp:t>{esc(EMPH.sub(r'\1', text))}</hp:t></hp:run>")
    parts, pos = [], 0
    for m in EMPH.finditer(text):
        if m.start() > pos:
            parts.append((text[pos:m.start()], char_id))
        parts.append((m.group(1), bold_id))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], char_id))
    return "".join(f'<hp:run charPrIDRef="{cid}"><hp:t>{esc(seg)}</hp:t></hp:run>'
                   for seg, cid in parts if seg)


def strip_linesegs(xml: str) -> str:
    """줄배치 캐시를 제거한다. 한컴이 열 때 다시 계산하므로 내용 길이가
    달라져도 레이아웃이 어긋나지 않는다 — 이 도구의 핵심."""
    return LINESEG.sub("", xml)


def spans(frag: str, tag: str):
    """frag 안의 <hp:{tag}> 요소 (시작, 끝) 목록 — 중첩 깊이를 추적한다."""
    out, pos = [], 0
    open_re = re.compile(rf"<hp:{tag}\b")
    both = re.compile(rf"<hp:{tag}\b|</hp:{tag}>")
    while pos < len(frag):
        m = open_re.search(frag, pos)
        if not m:
            break
        depth, end = 0, None
        for t in both.finditer(frag[m.start():]):
            depth += 1 if t.group().startswith(f"<hp:{tag}") else -1
            if depth == 0:
                end = m.start() + t.end()
                break
        if end is None:
            break
        out.append((m.start(), end))
        pos = end
    return out


def set_text(frag: str, text: str, bold_map: dict | None = None) -> str:
    """조각의 첫 문단 텍스트를 갈아끼운다. `**굵게**` 는 런을 나눠 처리한다."""
    if "**" in text:
        m = re.search(r'<hp:run\b[^>]*charPrIDRef="(\d+)"[^>]*>', frag)
        if m:
            runs = emphasis_runs(text, m.group(1), bold_map)
            first = m.start()
            last = frag.rfind("</hp:run>")
            if last > first:
                return frag[:first] + runs + frag[last + len("</hp:run>"):]
        text = EMPH.sub(r"\1", text)          # 런을 못 찾으면 별표만 제거
    done = [False]

    def rep(m):
        if done[0]:
            return "<hp:t></hp:t>"
        done[0] = True
        return f"<hp:t>{esc(text)}</hp:t>"

    out = T_RE.sub(rep, frag)
    if not done[0]:                      # <hp:t> 가 없으면 첫 run 에 만들어 넣는다
        out = re.sub(r"(<hp:run[^>]*>)", rf"\1<hp:t>{esc(text)}</hp:t>", out, count=1)
    return out


def fill_cells(frag: str, values: list, bold_map: dict | None = None) -> str:
    """표 조각의 셀에 순서대로 값을 넣는다. None 이면 그대로 둔다."""
    cells = spans(frag, "tc")
    out = frag
    for i in range(len(cells) - 1, -1, -1):        # 뒤에서부터 = 오프셋 안 깨짐
        if i >= len(values) or values[i] is None:
            continue
        st, en = cells[i]
        out = out[:st] + set_text(out[st:en], values[i], bold_map) + out[en:]
    return out


def fill_cell_paragraphs(frag: str, cell_idx: int, lines: list,
                         bold_map: dict | None = None) -> str:
    """한 셀의 문단들을 프로토타입 복제로 갈아끼운다(줄 수 = 내용 수)."""
    cells = spans(frag, "tc")
    if cell_idx >= len(cells):
        return frag
    st, en = cells[cell_idx]
    cell = frag[st:en]
    inner_paras = spans(cell, "p")
    if not inner_paras:
        return frag
    proto = cell[inner_paras[0][0]:inner_paras[0][1]]
    built = "".join(set_text(proto, ln, bold_map) for ln in lines) \
        or set_text(proto, "")
    new_cell = cell[:inner_paras[0][0]] + built + cell[inner_paras[-1][1]:]
    return frag[:st] + new_cell + frag[en:]


def page_break() -> str:
    """빈 문단에 쪽 나눔을 걸어 다음 쪽으로 넘긴다."""
    return ('<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="1" '
            'columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
            "<hp:t></hp:t></hp:run></hp:p>")


def renumber(xml: str, start: int = 1000) -> str:
    """문단 id 를 문서 안에서 고유하게 다시 매긴다."""
    n = [start]

    def rep(m):
        n[0] += 1
        return f'{m.group(1)}{n[0]}"'

    return re.sub(r'(<hp:p\b[^>]*?\bid=")\d+"', rep, xml)


def image_size(path: Path) -> tuple[int, int]:
    """PNG/JPEG 픽셀 크기를 표준 라이브러리만으로 읽는다."""
    b = path.read_bytes()
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    if b[:2] == b"\xff\xd8":                       # JPEG
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            mk = b[i + 1]
            if mk in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return (int.from_bytes(b[i + 7:i + 9], "big"),
                        int.from_bytes(b[i + 5:i + 7], "big"))
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    raise ValueError(f"PNG/JPEG 가 아니거나 크기를 읽을 수 없다: {path}")


def make_image(proto: str, item_id: str, px_w: int, px_h: int,
               max_w: int = 42000) -> str:
    """이미지 조각을 새 그림으로 바꾼다 — 원본 비율을 지키고 본문 폭에 맞춘다."""
    org_w, org_h = px_w * 75, px_h * 75          # 1px ≈ 75 HWPUNIT (96dpi 기준)
    disp_w = min(org_w, max_w)
    disp_h = max(1, round(org_h * disp_w / org_w))
    out = re.sub(r'binaryItemIDRef="[^"]*"', f'binaryItemIDRef="{item_id}"', proto)
    out = re.sub(r'(<hp:sz width=")\d+(" widthRelTo="ABSOLUTE" height=")\d+',
                 rf'\g<1>{disp_w}\g<2>{disp_h}', out)
    out = re.sub(r'<hp:orgSz width="\d+" height="\d+"/>',
                 f'<hp:orgSz width="{org_w}" height="{org_h}"/>', out)
    out = re.sub(r'<hp:curSz width="\d+" height="\d+"/>',
                 f'<hp:curSz width="{disp_w}" height="{disp_h}"/>', out)
    out = re.sub(r"<hp:imgRect>.*?</hp:imgRect>",
                 f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{org_w}" y="0"/>'
                 f'<hc:pt2 x="{org_w}" y="{org_h}"/><hc:pt3 x="0" y="{org_h}"/>'
                 f"</hp:imgRect>", out, flags=re.S)
    out = re.sub(r'<hp:imgClip[^/]*/>',
                 f'<hp:imgClip left="0" right="{org_w}" top="0" bottom="{org_h}"/>', out)
    out = re.sub(r'<hp:imgDim[^/]*/>',
                 f'<hp:imgDim dimwidth="{org_w}" dimheight="{org_h}"/>', out)
    return out


CELL_PAD = 800          # 셀 좌우 여백 여유(HWPUNIT)


def text_width(text: str, char_h: int = 2000) -> int:
    """글 한 줄이 차지할 대략적인 폭(HWPUNIT).

    한글 한 자의 자폭은 대략 글자 크기(1em)와 같고, 영문·숫자는 그 절반이다.
    글자 크기를 인자로 받는 게 핵심 — 배너(큰 글꼴)와 표 본문(작은 글꼴)에
    같은 상수를 쓰면 표 높이가 몇 배로 부풀려진다(실측).
    """
    korean = sum(1 for c in text if ord(c) > 0x7F)
    ascii_n = len(text) - korean
    return round(korean * char_h + ascii_n * char_h * 0.5) + CELL_PAD


def widen_banner(frag: str, title_cell: int, title: str, body_width: int,
                 heights: dict | None = None) -> str:
    """배너의 제목 칸을 제목 길이에 맞게 넓힌다.

    배너 템플릿의 칸 폭은 '원본 제목 길이'에 맞춰 굳어 있다(실측: 절 배너의
    제목 칸이 38.5mm — 원본 제목 '기본 방향' 4자 기준). 그대로 두면 긴 제목이
    서너 줄로 접혀 배너가 뭉개진다.
    """
    cells = spans(frag, "tc")
    if title_cell >= len(cells) or not body_width:
        return frag
    widths = []
    for st, en in cells:
        m = re.search(r'<hp:cellSz width="(\d+)"', frag[st:en])
        widths.append(int(m.group(1)) if m else 0)
    others = sum(w for i, w in enumerate(widths) if i != title_cell)
    st0, en0 = cells[title_cell]
    ch = first_char_height(frag[st0:en0], heights or {}, 2000)
    want = max(widths[title_cell], text_width(title, ch))
    new_w = min(want, max(body_width - others, widths[title_cell]))
    if new_w == widths[title_cell]:
        return frag
    st, en = cells[title_cell]
    cell = re.sub(r'(<hp:cellSz width=")\d+(")', rf'\g<1>{new_w}\g<2>',
                  frag[st:en], count=1)
    out = frag[:st] + cell + frag[en:]
    total = others + new_w
    return re.sub(r'(<hp:sz width=")\d+(")', rf'\g<1>{total}\g<2>', out, count=1)


# ─────────────────────────── 내용 파싱 ───────────────────────────

TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
LIST_ITEM = re.compile(r"^(\s*)[-*]\s+(.*)$")

# 들여쓰기 깊이 → 층위. 원고에 ㅇ/- 를 직접 쓰지 않고 목록 들여쓰기만으로
# 계층을 표현할 수 있게 한다(공문서 편집기들이 쓰는 관행).
INDENT_LEVELS = ["bullet", "dash", "dash"]


def parse_content(text: str) -> list:
    """마크다운스러운 원고를 블록 목록으로 바꾼다."""
    blocks, i, lines = [], 0, text.splitlines()
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("::: "):                       # 제목 있는 박스
            title = s[4:].strip()
            body = []
            i += 1
            while i < len(lines) and lines[i].strip() != ":::":
                if lines[i].strip():
                    body.append(lines[i].strip())
                i += 1
            blocks.append({"type": "titled_box", "title": title, "body": body})
            i += 1                                     # 닫는 ':::' 소비
        elif TABLE_ROW.match(ln):                      # 표
            rows = []
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                cells = [c.strip() for c in TABLE_ROW.match(lines[i]).group(1).split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append({"type": "table", "rows": rows})
            continue
        elif s.startswith("### "):
            rest = s[4:].strip()
            m = re.match(r"^(\S+)\s+(.*)$", rest)
            num, title = (m.group(1), m.group(2)) if m else ("", rest)
            blocks.append({"type": "section", "num": num, "title": title})
            i += 1
        elif s.startswith("## "):
            blocks.append({"type": "chapter", "title": s[3:].strip()})
            i += 1
        elif s.startswith("# "):
            blocks.append({"type": "cover", "title": s[2:].strip()})
            i += 1
        elif s.startswith("!["):
            m = re.search(r"\((.*?)\)", s)
            blocks.append({"type": "image", "path": m.group(1) if m else ""})
            i += 1
        else:
            m = LIST_ITEM.match(ln)
            if m and not s.startswith("* "):        # '* 각주' 는 기존 각주 층위
                depth = len(m.group(1)) // 2
                lvl = INDENT_LEVELS[min(depth, len(INDENT_LEVELS) - 1)]
                blocks.append({"type": "para", "text": m.group(2).rstrip(),
                               "level": lvl, "depth": depth})
            else:
                blocks.append({"type": "para", "text": ln.rstrip()})
            i += 1
    return blocks


# ─────────────────────────── render ───────────────────────────

def render(specdir: Path, content: Path, out: Path,
           cover_page: bool = False, toc: bool = False,
           org: str = "", date: str = "") -> dict:
    spec = json.loads((specdir / "spec.json").read_text(encoding="utf-8"))
    tpl = specdir / "templates"
    base = specdir / "base.hwpx"
    head, inner, tail = load_section(base)

    def T(name):
        return strip_linesegs((tpl / name).read_text(encoding="utf-8"))

    bold_map = build_bold_map(base)
    body_w = spec.get("body_width", 0)
    heights = build_char_heights(base)

    def para(kind: str, text: str) -> str:
        lv = spec["levels"].get(kind) or spec["levels"].get("plain")
        if lv is None:
            lv = {"paraPr": "0", "charPr": "0"}
        return (f'<hp:p id="0" paraPrIDRef="{lv["paraPr"]}" styleIDRef="0" '
                f'pageBreak="0" columnBreak="0" merged="0">'
                f'{emphasis_runs(text, lv["charPr"], bold_map)}</hp:p>')

    parts = [T(spec["page"]["template"])]                  # 페이지 설정 문단
    used = Counter()
    new_images: list[tuple[str, Path]] = []                # (item_id, 파일경로)

    banners = spec.get("banners", {})
    cover_t = next((v["template"] for k, v in banners.items() if k.startswith("banner_3x")), None)
    chap_t = next((v["template"] for k, v in banners.items() if v["cells"] == 1), None)
    sect_t = next((v["template"] for k, v in banners.items() if v["cells"] == 3
                   and not k.startswith("banner_3x")), None)
    blocks_ = spec.get("blocks", {})
    callout_t = blocks_.get("callout", {}).get("template")
    titled_t = next((v["template"] for k, v in blocks_.items()
                     if k.startswith("titled_box")), None)
    # 데이터 표는 열 수가 가장 흔한 것을 기본으로 쓴다
    table_t = next((v["template"] for v in spec.get("tables", {}).values()), None)

    blocks = parse_content(content.read_text(encoding="utf-8"))

    # 표지 페이지 — 제목을 독립 쪽으로 세우고 아래에 날짜·기관을 둔다
    if cover_page:
        cov = next((b for b in blocks if b["type"] == "cover"), None)
        if cov and cover_t:
            parts.append(fill_cells(T(cover_t), [None, cov["title"], None],
                                    bold_map))
            for line in (date, org):
                if line:
                    parts.append(para("plain", line))
            parts.append(page_break())
            blocks = [b for b in blocks if b is not cov]

    # 목차 — 장(##) 제목을 모아 한 쪽으로
    if toc:
        chapters = [b["title"] for b in blocks if b["type"] == "chapter"]
        if chapters:
            parts.append(para("plain", "목  차"))
            parts.append(para("plain", ""))
            for c in chapters:
                parts.append(para("bullet", c))
            parts.append(page_break())

    for b in blocks:
        t = b["type"]
        if t == "cover" and cover_t:
            parts.append(fill_cells(T(cover_t), [None, b["title"], None], bold_map))
        elif t == "chapter" and chap_t:
            frag = fill_cells(T(chap_t), [b["title"]], bold_map)
            parts.append(widen_banner(frag, 0, b["title"], body_w, heights))
        elif t == "section" and sect_t:
            frag = fill_cells(T(sect_t), [b["num"], None, b["title"]], bold_map)
            parts.append(widen_banner(frag, 2, b["title"], body_w, heights))
        elif t == "titled_box" and titled_t:
            frag = T(titled_t)
            cells = spans(frag, "tc")
            body_idx = max(range(len(cells)),
                           key=lambda i: len(text_of(frag[cells[i][0]:cells[i][1]])))
            frag = fill_cells(frag, [b["title"] if i == 1 else None
                                     for i in range(len(cells))], bold_map)
            parts.append(fill_cell_paragraphs(frag, body_idx, b["body"], bold_map))
        elif t == "table" and table_t and b["rows"]:
            parts.append(build_table(T(table_t), b["rows"], bold_map, heights))
        elif t == "image":
            obj = spec.get("objects", {}).get("image")
            src = (content.parent / b["path"]).expanduser()
            if obj and b["path"] and src.is_file():
                item_id = f"docspec{len(new_images) + 1}"
                pw, ph = image_size(src)
                parts.append(make_image(T(obj["template"]), item_id, pw, ph))
                new_images.append((item_id, src))
            elif obj and not b["path"]:
                parts.append(T(obj["template"]))
            else:
                print(f"  경고: 이미지를 찾지 못해 건너뛴다 — {b['path']}",
                      file=sys.stderr)
                used["image_missing"] += 1
                continue
        elif t == "para":
            txt = b["text"]
            if b.get("level"):                     # 들여쓰기로 층위가 정해진 항목
                lv = spec["levels"].get(b["level"])
                mk = (lv or {}).get("marker") or ""
                pad = "   " * b.get("depth", 0)
                parts.append(para(b["level"], f"{pad}{mk} {txt}".rstrip()))
                used[t] += 1
                continue
            kind = classify(txt.strip())
            if kind == "arrow" and callout_t:
                parts.append(fill_cells(T(callout_t), [txt.strip()], bold_map))
            else:
                parts.append(para(kind, txt))
        used[t] += 1

    body = "".join(parts)
    # 셀 줄바꿈을 BREAK 로 강제한다.
    # 레퍼런스 셀 상당수가 lineWrap="SQUEEZE"(긴 글을 한 줄에 욱여넣으려 자간을
    # 줄임)인데, 원본은 글이 짧아 티가 안 났다. 새 내용은 길이가 자유로우므로
    # 그대로 두면 글자가 서로 겹쳐 찍힌다(실사용에서 발생). 짧은 글에서는
    # BREAK 와 SQUEEZE 의 결과가 같으므로 손해가 없다.
    body = body.replace('lineWrap="SQUEEZE"', 'lineWrap="BREAK"')
    section = head + renumber(body) + tail
    build_package(base, out, section, new_images)
    return {"blocks": dict(used), "out": str(out),
            "images": [i for i, _ in new_images]}


def build_table(frag: str, rows: list, bold_map: dict | None = None,
                heights: dict | None = None) -> str:
    """표를 내용 크기(행×열)에 맞춰 다시 조립한다.

    원본 일정표는 '분야' 라벨 칸이 세로 병합돼 있다. 그 행을 그대로 복제하면
    칸 수가 모자라 내용이 밀린다(실측). 그래서 병합 없는 깨끗한 셀 하나를
    원자 단위로 삼아 모든 칸을 새로 찍는다.
    """
    trs = spans(frag, "tr")
    if not trs:
        return frag
    ncol = max(len(r) for r in rows)
    nrow = len(rows)

    # 병합 없는 셀을 원형으로 고른다(머리행용 / 본문용).
    def clean_cells(tr_xml):
        out = []
        for st, en in spans(tr_xml, "tc"):
            c = tr_xml[st:en]
            sp = re.search(r'<hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"', c)
            if sp and sp.group(1) == "1" and sp.group(2) == "1":
                out.append(c)
        return out

    head_pool = clean_cells(frag[trs[0][0]:trs[0][1]])
    body_pool = []
    for st, en in trs[1:]:
        body_pool = clean_cells(frag[st:en])
        if body_pool:
            break
    head_proto = head_pool[-1] if head_pool else (body_pool[0] if body_pool else None)
    body_proto = body_pool[-1] if body_pool else head_proto
    if head_proto is None:
        return frag

    total_w = 0
    sz = re.search(r'<hp:sz width="(\d+)"', frag)
    if sz:
        total_w = int(sz.group(1))
    cell_w = total_w // ncol if total_w else 0

    proto_h = 0
    m_h = re.search(r'<hp:cellSz width="\d+" height="(\d+)"', body_proto or "")
    if m_h:
        proto_h = int(m_h.group(1))

    body_ch = first_char_height(body_proto or "", heights or {}, 1000)
    head_ch = first_char_height(head_proto or "", heights or {}, body_ch)

    def cell_height(n_lines: int, ch: int) -> int:
        # 여백 포함 한 줄 높이 + 추가 줄마다 줄간격
        return proto_h + max(0, n_lines - 1) * round(ch * 1.7)

    def lines_needed(text: str, ch: int) -> int:
        """칸 폭에 견줘 몇 줄이 될지 어림한다.

        글자 크기를 칸마다 받는다 — 일차 제목행처럼 본문보다 큰 글꼴을 쓰는
        행을 본문 글꼴로 재면 줄 수가 모자라 칸이 낮게 잡힌다(검문에 걸림).
        """
        if not text or not cell_w:
            return 1
        usable = max(cell_w - CELL_PAD, 1000)
        return max(1, -(-(text_width(text, ch) - CELL_PAD) // usable))

    def make_cell(proto, text, col, row, row_lines, ch):
        c = set_text(proto, text, bold_map)
        if proto_h:
            # 원본 한 줄 높이(여백 포함)에 '추가 줄'만큼 줄간격을 더한다.
            # 한 줄 높이를 줄 수만큼 곱하면 여백이 줄마다 중복돼 칸이 과하게
            # 커진다(실측: 4줄짜리가 45mm → 실제 필요한 건 27mm 남짓).
            c = re.sub(r'(<hp:cellSz width="\d+" height=")\d+(")',
                       rf'\g<1>{cell_height(row_lines, ch)}\g<2>', c, count=1)
        c = re.sub(r'<hp:cellAddr colAddr="\d+" rowAddr="\d+"/>',
                   f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>', c)
        c = re.sub(r'<hp:cellSpan colSpan="\d+" rowSpan="\d+"/>',
                   '<hp:cellSpan colSpan="1" rowSpan="1"/>', c)
        if cell_w:
            c = re.sub(r'(<hp:cellSz width=")\d+(")', rf'\g<1>{cell_w}\g<2>', c)
        return c

    built, total_h = [], 0
    for r_i, row in enumerate(rows):
        proto = head_proto if r_i == 0 else body_proto
        # 한 행의 높이는 그 행에서 가장 많은 줄을 쓰는 칸에 맞춘다
        ch = head_ch if r_i == 0 else body_ch
        row_lines = max((lines_needed(row[c] if c < len(row) else "", ch)
                         for c in range(ncol)), default=1)
        total_h += cell_height(row_lines, ch)
        cells = "".join(
            make_cell(proto, row[c_i] if c_i < len(row) else "", c_i, r_i,
                      row_lines, ch)
            for c_i in range(ncol))
        built.append(f"<hp:tr>{cells}</hp:tr>")

    new = frag[:trs[0][0]] + "".join(built) + frag[trs[-1][1]:]
    if total_h:
        new = re.sub(r'(<hp:sz width="\d+" widthRelTo="ABSOLUTE" height=")\d+(")',
                     rf'\g<1>{total_h}\g<2>', new, count=1)
    new = re.sub(r'rowCnt="\d+"', f'rowCnt="{nrow}"', new, count=1)
    new = re.sub(r'colCnt="\d+"', f'colCnt="{ncol}"', new, count=1)
    return new


def build_package(base: Path, out: Path, section: str,
                  images: list | None = None) -> None:
    """base.hwpx 의 header/BinData 를 그대로 쓰고 본문만 갈아끼운다.
    새 이미지는 BinData 에 넣고 content.hpf 에 등록한다."""
    images = images or []
    texts = [html.unescape(INNER_TAG.sub("", t)) for t in T_RE.findall(section)]
    prv = "\n".join(t for t in texts if t.strip())
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "Contents/section0.xml":
                data = section.encode("utf-8")
            elif item.filename == "Contents/content.hpf" and images:
                txt = data.decode("utf-8")
                add = "".join(
                    f'<opf:item id="{iid}" href="BinData/{iid}{p.suffix.lower()}" '
                    f'media-type="image/{p.suffix.lower().lstrip(".").replace("jpg","jpeg")}" '
                    f'isEmbeded="1"/>' for iid, p in images)
                txt = txt.replace("</opf:manifest>", add + "</opf:manifest>")
                data = txt.encode("utf-8")
            elif item.filename == "Preview/PrvText.txt":
                data = prv.encode("utf-8")
            elif re.fullmatch(r"Contents/section[1-9]\d*\.xml", item.filename):
                continue                                  # 본문은 한 섹션으로 재구성
            if item.filename == "mimetype":
                zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(item, data)
        for iid, src in images:
            # 압축 시각을 고정한다. writestr 에 문자열 이름을 주면 '현재 시각'이
            # 박혀, 같은 입력인데도 파일 바이트가 매번 달라진다(실측). 나머지
            # 엔트리와 같은 1980-01-01 로 맞춰 재현 가능하게 만든다.
            info = zipfile.ZipInfo(f"BinData/{iid}{src.suffix.lower()}",
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(info, src.read_bytes())


def cmd_render(a) -> int:
    r = render(Path(a.spec), Path(a.content), Path(a.out),
               cover_page=getattr(a, "cover_page", False),
               toc=getattr(a, "toc", False),
               org=getattr(a, "org", ""), date=getattr(a, "date", ""))
    print(f"조판 완료 → {r['out']}")
    print(f"  블록: {r['blocks']}")
    q = lint_document(Path(a.out))          # 조판 직후 자동 검문
    for e in q["errors"]:
        print(f"  [오류] {e}", file=sys.stderr)
    for w in q["warnings"][:10]:
        print(f"  [경고] {w}", file=sys.stderr)
    if q["errors"]:
        print("  => 품질 검문 실패. 전달하지 마라.", file=sys.stderr)
        return 2
    return 0



# ─────────────────────────── 조판 품질 검문 ───────────────────────────

def lint_document(path: Path) -> dict:
    """조판 결과가 '내용에 맞게' 짜였는지 검사한다.

    결정론(같은 입력 → 같은 바이트)은 품질을 보장하지 못한다. 지금까지 실제로
    난 사고 셋은 모두 결정론적이었다 — 매번 똑같이 깨졌다. 그래서 레이아웃
    자체를 검사한다. 전부 XML 에서 계산 가능한 것들이다.
    """
    head, inner, tail = load_section(path)
    heights = build_char_heights(path)
    errors, warns = [], []

    if "<hp:linesegarray>" in inner:
        errors.append("줄배치 캐시가 남아 있다 — 옛 레이아웃이 그대로 굳는다")
    n_sq = inner.count('lineWrap="SQUEEZE"')
    if n_sq:
        errors.append(f"lineWrap=SQUEEZE {n_sq}곳 — 자간을 줄여 글자가 겹쳐 찍힌다")

    body_w = max([int(m.group(1)) for m in
                  re.finditer(r'<hp:sz width="(\d+)" widthRelTo="ABSOLUTE"', inner)]
                 or [0])

    for ts, te in spans(inner, "tbl"):
        tbl = inner[ts:te]
        rc = re.search(r'rowCnt="(\d+)" colCnt="(\d+)"', tbl)
        if not rc:
            continue
        rows, cols = int(rc.group(1)), int(rc.group(2))
        cells = spans(tbl, "tc")
        merged = any(
            (mm := re.search(r'colSpan="(\d+)" rowSpan="(\d+)"', tbl[cs:ce]))
            and (mm.group(1) != "1" or mm.group(2) != "1")
            for cs, ce in cells)
        if not merged and len(cells) != rows * cols:
            errors.append(f"표 {rows}x{cols} 인데 칸이 {len(cells)}개 "
                          f"(있어야 할 {rows * cols}개) — 내용이 밀린다")
        for cs, ce in cells:
            cell = tbl[cs:ce]
            txt = text_of(cell).strip()
            sz = re.search(r'<hp:cellSz width="(\d+)" height="(\d+)"', cell)
            if not sz or not txt:
                continue
            cw, chh = int(sz.group(1)), int(sz.group(2))
            fh = first_char_height(cell, heights, 1000)
            usable = max(cw - CELL_PAD, 1000)
            need_lines = max(1, -(-(text_width(txt, fh) - CELL_PAD) // usable))
            line_adv = round(fh * 1.7)
            need_h = line_adv * need_lines
            if chh < need_h * 0.75:
                warns.append(f"칸이 낮다({chh / 283.5:.0f}mm, {need_lines}줄엔 "
                             f"{need_h / 283.5:.0f}mm 필요): {txt[:24]!r}")
            elif need_lines <= 2 and chh > need_h * 3 and chh > 40 * 283.5:
                warns.append(f"칸이 지나치게 높다({chh / 283.5:.0f}mm, 내용 "
                             f"{len(txt)}자) — 빈 박스가 된다: {txt[:24]!r}")
            if body_w and cw > body_w * 1.02:
                warns.append(f"칸 폭이 본문 폭을 넘는다({cw / 283.5:.0f}mm)")

    for ps, pe in spans(inner, "pic"):
        pic = inner[ps:pe]
        m = re.search(r'<hp:sz width="(\d+)"', pic)
        if m and body_w and int(m.group(1)) > body_w * 1.02:
            warns.append(f"그림이 본문 폭을 넘는다({int(m.group(1)) / 283.5:.0f}mm)")

    return {"file": str(path), "errors": errors, "warnings": warns,
            "ok": not errors}


def cmd_lint(a) -> int:
    r = lint_document(Path(a.file))
    if r["errors"]:
        print(f"조판 품질 검문 실패 — 오류 {len(r['errors'])}건")
        for e in r["errors"]:
            print(f"  [오류] {e}")
    if r["warnings"]:
        print(f"경고 {len(r['warnings'])}건")
        for w in r["warnings"][:20]:
            print(f"  [경고] {w}")
        if len(r["warnings"]) > 20:
            print(f"  … 외 {len(r['warnings']) - 20}건")
    if r["ok"] and not r["warnings"]:
        print("조판 품질 검문 통과 — 레이아웃이 내용과 맞는다")
    elif r["ok"]:
        print("오류 없음(경고만) — 전달 가능")
    return 0 if r["ok"] else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="문서 편집 규범 추출·조판기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze", help="레퍼런스 문서에서 편집 규범 추출")
    p.add_argument("ref")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_analyze)
    r = sub.add_parser("render", help="추출한 규범대로 새 내용을 조판")
    r.add_argument("spec")
    r.add_argument("content")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--cover-page", action="store_true", dest="cover_page",
                   help="제목을 독립 표지 쪽으로 세운다")
    r.add_argument("--toc", action="store_true", help="장 제목으로 목차 쪽 생성")
    r.add_argument("--org", default="", help="표지에 넣을 기관·작성주체")
    r.add_argument("--date", default="", help="표지에 넣을 날짜")
    r.set_defaults(fn=cmd_render)
    q = sub.add_parser("lint", help="조판 결과의 레이아웃 품질 검문")
    q.add_argument("file")
    q.set_defaults(fn=cmd_lint)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
