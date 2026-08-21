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
MARKERS = [
    ("h_square", "□"),
    ("bullet", "ㅇ"),
    ("arrow", "⇒"),
    ("record", "￭"),
    ("tri", "▸"),
    ("note_ref", "※"),
    ("dash", "-"),
    ("note", "*"),
]


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
    for name, mk in MARKERS:
        if s.startswith(mk):
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
            "marker": dict(MARKERS).get(kind, ""),
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


# ─────────────────────────── 내용 파싱 ───────────────────────────

TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


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
            blocks.append({"type": "para", "text": ln.rstrip()})
            i += 1
    return blocks


# ─────────────────────────── render ───────────────────────────

def render(specdir: Path, content: Path, out: Path) -> dict:
    spec = json.loads((specdir / "spec.json").read_text(encoding="utf-8"))
    tpl = specdir / "templates"
    base = specdir / "base.hwpx"
    head, inner, tail = load_section(base)

    def T(name):
        return strip_linesegs((tpl / name).read_text(encoding="utf-8"))

    bold_map = build_bold_map(base)

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

    for b in parse_content(content.read_text(encoding="utf-8")):
        t = b["type"]
        if t == "cover" and cover_t:
            parts.append(fill_cells(T(cover_t), [None, b["title"], None], bold_map))
        elif t == "chapter" and chap_t:
            parts.append(fill_cells(T(chap_t), [b["title"]], bold_map))
        elif t == "section" and sect_t:
            parts.append(fill_cells(T(sect_t), [b["num"], None, b["title"]], bold_map))
        elif t == "titled_box" and titled_t:
            frag = T(titled_t)
            cells = spans(frag, "tc")
            body_idx = max(range(len(cells)),
                           key=lambda i: len(text_of(frag[cells[i][0]:cells[i][1]])))
            frag = fill_cells(frag, [b["title"] if i == 1 else None
                                     for i in range(len(cells))], bold_map)
            parts.append(fill_cell_paragraphs(frag, body_idx, b["body"], bold_map))
        elif t == "table" and table_t and b["rows"]:
            parts.append(build_table(T(table_t), b["rows"], bold_map))
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


def build_table(frag: str, rows: list, bold_map: dict | None = None) -> str:
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

    def make_cell(proto, text, col, row):
        c = set_text(proto, text, bold_map)
        c = re.sub(r'<hp:cellAddr colAddr="\d+" rowAddr="\d+"/>',
                   f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>', c)
        c = re.sub(r'<hp:cellSpan colSpan="\d+" rowSpan="\d+"/>',
                   '<hp:cellSpan colSpan="1" rowSpan="1"/>', c)
        if cell_w:
            c = re.sub(r'(<hp:cellSz width=")\d+(")', rf'\g<1>{cell_w}\g<2>', c)
        return c

    built = []
    for r_i, row in enumerate(rows):
        proto = head_proto if r_i == 0 else body_proto
        cells = "".join(
            make_cell(proto, row[c_i] if c_i < len(row) else "", c_i, r_i)
            for c_i in range(ncol))
        built.append(f"<hp:tr>{cells}</hp:tr>")

    new = frag[:trs[0][0]] + "".join(built) + frag[trs[-1][1]:]
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
            zout.writestr(f"BinData/{iid}{src.suffix.lower()}", src.read_bytes())


def cmd_render(a) -> int:
    r = render(Path(a.spec), Path(a.content), Path(a.out))
    print(f"조판 완료 → {r['out']}")
    print(f"  블록: {r['blocks']}")
    return 0


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
    r.set_defaults(fn=cmd_render)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
