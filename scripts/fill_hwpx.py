#!/usr/bin/env python3
"""HWPX 원본 보존 채우기 — 서식(양식) 문서의 텍스트만 교체하고 나머지는 바이트 그대로 유지.

kordoc(https://github.com/chrisryugj/kordoc)의 fillHwpx + patchZipEntries를
Python 표준 라이브러리만으로 포팅한 것이다. 설계 원칙:

1. XML을 DOM으로 재직렬화하지 않는다 — 정규식 토크나이저로 <hp:t> 위치만 추적하고
   문자열 splice로 교체한다. 네임스페이스/속성 순서/공백/self-closing 스타일이
   원본 그대로 보존되므로 fix_namespaces.py가 필요 없다.
2. ZIP을 통째로 재압축하지 않는다 — 변경된 section XML 엔트리만 재작성하고
   나머지 엔트리(이미지, header.xml, mimetype 등)는 원본 바이트를 그대로 복사한다.
   엔트리 순서/압축 방식/mimetype 첫 엔트리 규약이 자동 보존된다.
3. LLM은 JSON만 만든다 — analyze가 채울 수 있는 타겟을 JSON으로 보여주고,
   LLM은 {라벨: 값} JSON을 작성하며, fill/verify가 결과를 JSON으로 보고한다.
   XML을 손으로 쓰는 단계가 없으므로 어떤 LLM에서도 같은 결과가 나온다.

채우기 전략 (kordoc 포팅):
  전략 0: 인셀 패턴 — 체크박스 □항목→☑항목, 괄호 빈칸 일반(  )통→일반(값)통,
          어노테이션 (한자：    )→(한자：값)
  전략 1: 인접 라벨-값 셀 — 테이블에서 라벨 셀 오른쪽 셀의 텍스트를 교체
          (첫 run의 charPrIDRef 유지 → 글꼴/크기/굵기 보존)
  전략 2: 헤더 행 패턴 — 첫 행이 전부 라벨이면 아래 행을 데이터로 채움
  전략 3: 인라인 "라벨: 값" — 테이블 밖 문단의 라벨 뒤 텍스트 교체

사용법:
    # 1) 채울 수 있는 타겟 분석 (LLM이 이 출력을 보고 values JSON을 작성)
    python3 fill_hwpx.py analyze form.hwpx

    # 2) 채우기 (values.json: {"성명": "홍길동", "연락처": "010-1234-5678"})
    python3 fill_hwpx.py fill form.hwpx output.hwpx --values values.json

    # 2-1) 좌표 직접 지정 (라벨 매칭이 안 통하는 복잡한 표 폴백)
    python3 fill_hwpx.py fill form.hwpx output.hwpx \
        --cells cells.json   # [{"table":0,"row":2,"col":1,"value":"텍스트"}]

    # 3) 문구 교체 — run 경계로 쪼개진 텍스트도 잡음 (내용 수정)
    python3 fill_hwpx.py replace doc.hwpx out.hwpx --map map.json

    # 4) 표 행 추가 — 기존 행 복제라서 스타일/너비/테두리 보존 (내용 추가)
    python3 fill_hwpx.py add-row doc.hwpx out.hwpx --table 1 --rows rows.json

    # 5) 검증 (값이 실제로 들어갔는지 + 비변경 엔트리 바이트 동일성)
    python3 fill_hwpx.py verify output.hwpx --values values.json --original form.hwpx

종료 코드: 0=성공, 1=오류, 2=채워진 항목 없음/검증 실패
"""

from __future__ import annotations  # Python 3.9 호환: str | None 등 어노테이션 지연 평가

import argparse
import io
import json
import re
import struct
import sys
import zipfile
import zlib

# ─── 라벨 인식 (kordoc recognize.ts 포팅) ───────────────────────────

# 한국 공문서 필드 라벨 키워드
LABEL_KEYWORDS = {
    "성명", "이름", "주소", "전화", "전화번호", "휴대폰", "핸드폰", "연락처",
    "생년월일", "주민등록번호", "소속", "직위", "직급", "부서",
    "이메일", "팩스", "학교", "학년", "반", "번호",
    "신청인", "대표자", "담당자", "작성자", "확인자", "승인자",
    "일시", "날짜", "기간", "장소", "목적", "사유", "비고",
    "금액", "수량", "단가", "합계", "계", "소계",
    "등록기준지", "본적", "위임인", "청구사유", "소명자료",
}

_SUPERSCRIPT_RE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰*※]+$")


def is_label_cell(text):
    """라벨처럼 보이는 셀인지 판별."""
    trimmed = _SUPERSCRIPT_RE.sub("", text.strip()).strip()
    if not trimmed or len(trimmed) > 30:
        return False
    for kw in LABEL_KEYWORDS:
        if kw in trimmed:
            return True
    compact = re.sub(r"\s", "", trimmed)
    if (re.fullmatch(r"[가-힣\s()（）·:：]+", trimmed)
            and 2 <= len(compact) <= 8 and not re.search(r"\d", trimmed)):
        return True
    if re.fullmatch(r"[가-힣A-Za-z\s]+[:：]", trimmed):
        return True
    return False


# ─── 매칭 유틸 (kordoc match.ts 포팅) ───────────────────────────────

def normalize_label(label):
    """라벨 정규화 — 콜론/공백/특수문자 제거, 비교용."""
    return re.sub(r"[:：\s()（）·]", "", label.strip())


def find_matching_key(cell_label, values):
    """정규화된 셀 라벨에 대한 최적 매칭 키.

    1) 정확 매칭  2) 접두사 매칭 (60% 이상 겹침, 가장 긴 매칭 우선)
    """
    if cell_label in values:
        return cell_label
    best_key, best_len = None, 0
    for key in values:
        if cell_label.startswith(key):
            if len(key) >= len(cell_label) * 0.6 and len(key) > best_len:
                best_len, best_key = len(key), key
        elif key.startswith(cell_label):
            if len(cell_label) >= len(key) * 0.6 and len(cell_label) > best_len:
                best_len, best_key = len(cell_label), key
    return best_key


def is_keyword_label(text):
    """값 셀이 키워드 라벨(하위 라벨)인지 — 채우면 안 되는 셀."""
    trimmed = _SUPERSCRIPT_RE.sub("", text.strip()).strip()
    if not trimmed or len(trimmed) > 15:
        return False
    return any(kw in trimmed for kw in LABEL_KEYWORDS)


CHECKBOX_TRUTHY = {"☑", "✓", "✔", "v", "V", "true", "1", "yes", "o", "O", ""}

_BRACKET_RE = re.compile(r"([가-힣A-Za-z]+)\(\s{1,}\)([가-힣A-Za-z]*)")
_CHECKBOX_RE = re.compile(r"□([가-힣A-Za-z]+)")
_ANNOTATION_RE = re.compile(r"\(([가-힣A-Za-z]+)[:：]\s{1,}\)")


def fill_in_cell_patterns(cell_text, values, matched_labels):
    """셀 텍스트의 인셀 패턴 교체 — 체크박스/괄호 빈칸/어노테이션.

    Returns: (교체된 텍스트, [{"key","label","value"}]) 또는 None
    """
    matches = []

    def bracket_sub(m):
        prefix, suffix = m.group(1), m.group(2)
        label = prefix + suffix
        norm = normalize_label(label)
        if norm in values:
            key = norm
        elif normalize_label(prefix) in values:
            key = normalize_label(prefix)
        else:
            return m.group(0)
        value = values[key]
        matched_labels.add(key)
        matches.append({"key": key, "label": label, "value": value})
        return f"{prefix}({value}){suffix}"

    def checkbox_sub(m):
        keyword = m.group(1)
        key = normalize_label(keyword)
        if key not in values:
            return m.group(0)
        if values[key].strip() not in CHECKBOX_TRUTHY:
            return m.group(0)
        matched_labels.add(key)
        matches.append({"key": key, "label": f"□{keyword}", "value": "☑"})
        return f"☑{keyword}"

    def annotation_sub(m):
        keyword = m.group(1)
        key = normalize_label(keyword)
        if key not in values:
            return m.group(0)
        value = values[key]
        matched_labels.add(key)
        matches.append({"key": key, "label": keyword, "value": value})
        return f"({keyword}：{value})"

    text = _BRACKET_RE.sub(bracket_sub, cell_text)
    text = _CHECKBOX_RE.sub(checkbox_sub, text)
    text = _ANNOTATION_RE.sub(annotation_sub, text)
    return (text, matches) if matches else None


def normalize_values(values):
    """입력 values 딕셔너리를 정규화된 키로 변환."""
    return {normalize_label(k): v for k, v in values.items()}


def resolve_unmatched(normalized_values, matched_labels, original_values):
    """매칭 안 된 라벨을 원본 키로 복원."""
    unmatched = []
    for k in normalized_values:
        if k in matched_labels:
            continue
        orig = next((o for o in original_values if normalize_label(o) == k), k)
        unmatched.append(orig)
    return unmatched


# ─── XML 토크나이저 (DOM 없는 구조 스캔) ────────────────────────────

class El:
    """바이트 오프셋을 보존하는 경량 XML 요소."""
    __slots__ = ("name", "qname", "start", "open_end", "content_start",
                 "content_end", "end", "self_closing", "children", "parent")

    def __init__(self, name, qname, start, open_end):
        self.name = name          # 로컬 태그명 (프리픽스 제거)
        self.qname = qname        # 원본 태그명 (hp:t 등)
        self.start = start        # '<' 위치
        self.open_end = open_end  # 여는 태그의 '>' 다음 위치
        self.content_start = open_end
        self.content_end = open_end
        self.end = open_end       # 닫는 태그의 '>' 다음 위치
        self.self_closing = False
        self.children = []
        self.parent = None


def scan_xml(xml):
    """XML 문자열을 스캔해 오프셋 보존 요소 트리를 만든다.

    속성 값 안의 '>' / 따옴표를 올바르게 처리한다. 검증기가 아니므로
    닫는 태그가 안 맞으면 가장 가까운 같은 이름의 조상을 닫는다.
    """
    root = El("#root", "#root", 0, 0)
    stack = [root]
    i, n = 0, len(xml)
    while True:
        lt = xml.find("<", i)
        if lt < 0:
            break
        if xml.startswith("<!--", lt):
            e = xml.find("-->", lt + 4)
            i = e + 3 if e >= 0 else n
            continue
        if xml.startswith("<![CDATA[", lt):
            e = xml.find("]]>", lt + 9)
            i = e + 3 if e >= 0 else n
            continue
        if xml.startswith("<?", lt):
            e = xml.find("?>", lt + 2)
            i = e + 2 if e >= 0 else n
            continue
        if xml.startswith("<!", lt):
            e = xml.find(">", lt + 2)
            i = e + 1 if e >= 0 else n
            continue
        # 따옴표를 존중하며 '>' 탐색
        j = lt + 1
        while j < n:
            c = xml[j]
            if c in "\"'":
                k = xml.find(c, j + 1)
                j = k + 1 if k >= 0 else n
            elif c == ">":
                break
            else:
                j += 1
        if j >= n:
            break
        tag_end = j + 1

        if xml[lt + 1] == "/":  # 닫는 태그
            local = xml[lt + 2:j].strip().split(":")[-1]
            for idx in range(len(stack) - 1, 0, -1):
                if stack[idx].name == local:
                    el = stack[idx]
                    el.content_end = lt
                    el.end = tag_end
                    del stack[idx:]
                    break
            i = tag_end
            continue

        self_closing = xml[j - 1] == "/"
        m = re.match(r"[^\s/>]+", xml[lt + 1:j])
        qname = m.group(0) if m else ""
        el = El(qname.split(":")[-1], qname, lt, tag_end)
        el.parent = stack[-1]
        stack[-1].children.append(el)
        if self_closing:
            el.self_closing = True
            el.content_start = el.content_end = el.end = tag_end
        else:
            el.content_start = tag_end
            stack.append(el)
        i = tag_end
    return root


def descendants(el, names):
    """문서 순서 깊이 우선으로 특정 로컬 태그명의 후손 요소를 yield."""
    if isinstance(names, str):
        names = (names,)
    stack = list(reversed(el.children))
    while stack:
        node = stack.pop()
        if node.name in names:
            yield node
        stack.extend(reversed(node.children))


def direct_children(el, name):
    return [c for c in el.children if c.name == name]


def ancestor_within(el, names, stop):
    """el과 stop 사이에 names 태그 조상이 있는지."""
    p = el.parent
    while p is not None and p is not stop:
        if p.name in names:
            return True
        p = p.parent
    return False


def under_tbl_within(el, stop):
    """el이 stop 내부의 중첩 <tbl> 안에 있는지 — 중첩 테이블 내용 보호용."""
    return ancestor_within(el, ("tbl",), stop)


def own_tnodes(p_el, reg):
    """이 문단에 직접 속한 <hp:t>만 (중첩 표/하위 문단 소속 제외)."""
    return [reg.get(t) for t in descendants(p_el, "t")
            if not ancestor_within(t, ("p",), p_el)]


# ─── 엔티티 인코딩/디코딩 ──────────────────────────────────────────

_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_ENTITY_RE = re.compile(r"&(#x[0-9A-Fa-f]+|#\d+|\w+);")
_INNER_TAG_RE = re.compile(r"<[^>]*>")


def decode_entities(s):
    def rep(m):
        e = m.group(1)
        if e.startswith("#x") or e.startswith("#X"):
            return chr(int(e[2:], 16))
        if e.startswith("#"):
            return chr(int(e[1:]))
        return _ENTITIES.get(e, m.group(0))
    return _ENTITY_RE.sub(rep, s)


def escape_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── 텍스트 노드 레지스트리 ────────────────────────────────────────

class TNode:
    """<hp:t> 요소의 현재 텍스트 상태. 모든 전략이 이 객체를 통해 읽고 쓴다."""
    __slots__ = ("el", "orig", "text")

    def __init__(self, el, xml):
        self.el = el
        raw = xml[el.content_start:el.content_end]
        self.orig = decode_entities(_INNER_TAG_RE.sub("", raw))
        self.text = self.orig


class Registry:
    """섹션 단위 TNode 캐시 — 같은 요소를 두 전략이 건드려도 상태가 일관됨."""

    def __init__(self, xml):
        self.xml = xml
        self._map = {}
        self.run_insertions = []  # [(run_el, text)] — <hp:t>가 없는 빈 run에 삽입

    def get(self, el):
        tn = self._map.get(id(el))
        if tn is None:
            tn = TNode(el, self.xml)
            self._map[id(el)] = tn
        return tn

    def cell_tnodes(self, tc):
        """셀 내 <hp:t> TNode 목록 (중첩 테이블 내부 제외)."""
        return [self.get(t) for t in descendants(tc, "t")
                if not under_tbl_within(t, tc)]

    def changed(self):
        return [tn for tn in self._map.values() if tn.text != tn.orig]


def extract_cell_text(tc, reg):
    """셀 텍스트 추출 — run/p/subList 순회, 중첩 테이블 제외, tab/br 반영."""
    parts = []

    def walk(el):
        for ch in el.children:
            if ch.name == "t":
                parts.append(reg.get(ch).text)
            elif ch.name in ("run", "r", "p", "subList"):
                walk(ch)
            elif ch.name == "tab":
                parts.append("\t")
            elif ch.name in ("br", "lineBreak"):
                parts.append("\n")
    walk(tc)
    return "".join(parts)


# ─── 텍스트 교체 (kordoc replaceCellText/setRunText 포팅) ──────────

def set_run_text(run, text, reg):
    """run의 <hp:t> 텍스트 교체. <hp:t>가 없는 빈 run이면 삽입 예약."""
    ts = [reg.get(t) for t in descendants(run, "t")
          if not under_tbl_within(t, run)]
    if ts:
        ts[0].text = text
        for tn in ts[1:]:
            tn.text = ""
        return
    # 한컴오피스가 HWP→HWPX 변환 시 빈 셀 run을 <hp:run charPrIDRef=".."/>로
    # 만들면서 <hp:t>를 생략한다 — 이때는 새 <hp:t>를 삽입해야 한다.
    if text:
        reg.run_insertions.append((run, text))


def cell_paragraphs(tc):
    """셀의 문단 목록 (중첩 테이블 내부 문단 제외)."""
    return [p for p in descendants(tc, "p") if not under_tbl_within(p, tc)]


def replace_cell_text(tc, new_value, reg):
    """셀 텍스트를 새 값으로 교체 — 스타일 보존 전략.

    1) 첫 문단 첫 run의 <hp:t>에 새 텍스트 (charPrIDRef 보존)
    2) 나머지 run의 <hp:t>는 빈 문자열
    3) 두 번째 이후 문단은 내용만 비움 (요소 유지 — 뷰어 호환)
    """
    paragraphs = cell_paragraphs(tc)
    if not paragraphs:
        return
    first_p = paragraphs[0]
    runs = [r for r in descendants(first_p, ("run", "r"))
            if not under_tbl_within(r, first_p)]
    if runs:
        set_run_text(runs[0], new_value, reg)
        for r in runs[1:]:
            set_run_text(r, "", reg)
    else:
        ts = [reg.get(t) for t in descendants(first_p, "t")
              if not under_tbl_within(t, first_p)]
        if ts:
            ts[0].text = new_value
            for tn in ts[1:]:
                tn.text = ""
    for p in paragraphs[1:]:
        for r in descendants(p, ("run", "r")):
            if not under_tbl_within(r, p):
                set_run_text(r, "", reg)
        for t in descendants(p, "t"):
            if not under_tbl_within(t, p):
                reg.get(t).text = ""


def prepend_cell_text(tc, text, reg):
    """셀 첫 <hp:t> 앞에 텍스트 삽입 — 어노테이션 보존.
    예: "(한자：金民秀)" → "김민수 (한자：金民秀)"
    """
    ts = reg.cell_tnodes(tc)
    if ts:
        ts[0].text = f"{text} {ts[0].text}"


def with_offsets(tnodes):
    """현재 텍스트 기준 글로벌 오프셋 계산."""
    out, off = [], 0
    for tn in tnodes:
        out.append((tn, off))
        off += len(tn.text)
    return out


def replace_text_range(tnodes, g_start, g_end, new_value):
    """여러 <hp:t>에 걸친 텍스트 범위 교체 — 첫 노드에 새 값, 나머지는 잘라냄."""
    replaced = False
    for tn, off in with_offsets(tnodes):
        node_start, node_end = off, off + len(tn.text)
        if node_end <= g_start or node_start >= g_end:
            continue
        local_start = max(0, g_start - node_start)
        local_end = min(len(tn.text), g_end - node_start)
        before, after = tn.text[:local_start], tn.text[local_end:]
        tn.text = before + (new_value if not replaced else "") + after
        replaced = True


def apply_text_replacements(tnodes, original_full, replaced_full):
    """공통 접두/접미를 제외한 변경 구간만 해당 노드에 반영."""
    if original_full == replaced_full:
        return
    if len(tnodes) == 1:
        tnodes[0].text = replaced_full
        return
    diff_start = 0
    while (diff_start < len(original_full) and diff_start < len(replaced_full)
           and original_full[diff_start] == replaced_full[diff_start]):
        diff_start += 1
    diff_end_orig, diff_end_repl = len(original_full), len(replaced_full)
    while (diff_end_orig > diff_start and diff_end_repl > diff_start
           and original_full[diff_end_orig - 1] == replaced_full[diff_end_repl - 1]):
        diff_end_orig -= 1
        diff_end_repl -= 1
    new_part = replaced_full[diff_start:diff_end_repl]
    replace_text_range(tnodes, diff_start, diff_end_orig, new_part)


# ─── 섹션 채우기 (전략 0~3) ────────────────────────────────────────

INLINE_RE = re.compile(r"([가-힣A-Za-z]{2,10})\s*[:：]\s*([^\n,;]{0,100})")


def fill_section(xml, values, matched_labels, filled, section_index):
    """한 section XML에 전략 0~3을 적용. 변경 없으면 None 반환."""
    root = scan_xml(xml)
    reg = Registry(xml)
    tables = list(descendants(root, "tbl"))

    # 전략 0: 인셀 패턴 — 전략 1보다 먼저 (어노테이션 보존을 위해)
    cell_pattern_applied = set()
    seen_cells = set()
    for tbl in tables:
        for tc in descendants(tbl, "tc"):
            if id(tc) in seen_cells or under_tbl_within(tc, tbl):
                continue
            seen_cells.add(id(tc))
            tnodes = reg.cell_tnodes(tc)
            full_text = "".join(tn.text for tn in tnodes)
            result = fill_in_cell_patterns(full_text, values, matched_labels)
            if not result:
                continue
            new_text, matches = result
            apply_text_replacements(tnodes, full_text, new_text)
            cell_pattern_applied.add(id(tc))
            for m in matches:
                filled.append({"label": m["label"], "value": m["value"],
                               "section": section_index, "row": -1, "col": -1,
                               "strategy": "in-cell"})

    for tbl in tables:
        rows = direct_children(tbl, "tr")

        # 전략 1: 인접 라벨-값 셀
        for row_idx, tr in enumerate(rows):
            cells = direct_children(tr, "tc")
            for col_idx in range(len(cells) - 1):
                label_text = extract_cell_text(cells[col_idx], reg)
                if not is_label_cell(label_text):
                    continue
                value_cell = cells[col_idx + 1]
                if is_keyword_label(extract_cell_text(value_cell, reg)):
                    continue
                norm = normalize_label(label_text)
                if not norm:
                    continue
                match_key = find_matching_key(norm, values)
                if match_key is None:
                    continue
                new_value = values[match_key]
                if id(value_cell) in cell_pattern_applied:
                    # 전략 0이 어노테이션을 채웠다면 값을 앞에 삽입 (보존)
                    prepend_cell_text(value_cell, new_value, reg)
                else:
                    replace_cell_text(value_cell, new_value, reg)
                matched_labels.add(match_key)
                filled.append({
                    "label": re.sub(r"[:：]\s*$", "", label_text.strip()),
                    "value": new_value, "section": section_index,
                    "row": row_idx, "col": col_idx, "strategy": "label-value",
                })

        # 전략 2: 헤더+데이터 행 (첫 행이 전부 라벨이면)
        if len(rows) >= 2:
            header_cells = direct_children(rows[0], "tc")
            all_labels = header_cells and all(
                0 < len(extract_cell_text(c, reg).strip()) <= 20
                and is_label_cell(extract_cell_text(c, reg).strip())
                for c in header_cells)
            if all_labels:
                for row_idx in range(1, len(rows)):
                    data_cells = direct_children(rows[row_idx], "tc")
                    for col_idx in range(min(len(header_cells), len(data_cells))):
                        header_label = normalize_label(
                            extract_cell_text(header_cells[col_idx], reg))
                        match_key = find_matching_key(header_label, values)
                        if match_key is None or match_key in matched_labels:
                            continue
                        new_value = values[match_key]
                        replace_cell_text(data_cells[col_idx], new_value, reg)
                        matched_labels.add(match_key)
                        filled.append({
                            "label": extract_cell_text(header_cells[col_idx], reg).strip(),
                            "value": new_value, "section": section_index,
                            "row": row_idx, "col": col_idx, "strategy": "header-row",
                        })

    # 전략 3: 인라인 "라벨: 값" (테이블 밖 문단)
    def inside_table(el):
        p = el.parent
        while p is not None:
            if p.name == "tbl":
                return True
            p = p.parent
        return False

    for p_el in descendants(root, "p"):
        if inside_table(p_el):
            continue
        # 이 문단이 표를 감싸는 anchor면 표 내부 <hp:t>는 제외 (표는 전략 0~2 담당)
        tnodes = [reg.get(t) for t in descendants(p_el, "t")
                  if not under_tbl_within(t, p_el)]
        full_text = "".join(tn.text for tn in tnodes)
        for m in INLINE_RE.finditer(full_text):
            raw_label = m.group(1)
            match_key = find_matching_key(normalize_label(raw_label), values)
            if match_key is None or match_key in matched_labels:
                continue
            new_value = values[match_key]
            value_start = m.start() + len(m.group(0)) - len(m.group(2))
            value_end = m.end()
            replace_text_range(tnodes, value_start, value_end, new_value)
            matched_labels.add(match_key)
            filled.append({"label": raw_label.strip(), "value": new_value,
                           "section": section_index, "row": -1, "col": -1,
                           "strategy": "inline"})
            break  # 교체 후 오프셋이 바뀌므로 문단당 1회

    # ─ 변경분을 문자열 splice로 반영 ─
    splices = build_splices(xml, reg)
    if not splices:
        return None
    return apply_splices(xml, splices)


def _nearest(el, name):
    """가장 가까운 특정 태그 조상."""
    p = el.parent
    while p is not None:
        if p.name == name:
            return p
        p = p.parent
    return None


def build_splices(xml, reg):
    """레지스트리의 변경분(텍스트 교체 + 빈 run 삽입)을 splice 목록으로 변환.

    텍스트가 바뀐 문단의 <hp:linesegarray>(한컴 줄배치 캐시)도 함께 제거한다.
    stale 캐시는 한컴이 '손상된 파일' 경고를 띄우는 원인 (PR #1 참조).
    수정된 문단의 캐시만 제거하므로 나머지 문단의 레이아웃 정보는 보존된다.
    """
    splices = []  # (start, end, replacement)
    for tn in reg.changed():
        el = tn.el
        if el.self_closing:
            if not tn.text:
                continue
            # <hp:t/> → <hp:t>text</hp:t>
            tag = xml[el.start:el.end]
            opening = re.sub(r"\s*/>$", ">", tag)
            splices.append((el.start, el.end,
                            f"{opening}{escape_text(tn.text)}</{el.qname}>"))
        else:
            splices.append((el.content_start, el.content_end,
                            escape_text(tn.text)))
    for run, text in reg.run_insertions:
        prefix = run.qname.split(":")[0] if ":" in run.qname else None
        t_qname = f"{prefix}:t" if prefix else "t"
        t_xml = f"<{t_qname}>{escape_text(text)}</{t_qname}>"
        if run.self_closing:
            tag = xml[run.start:run.end]
            opening = re.sub(r"\s*/>$", ">", tag)
            splices.append((run.start, run.end,
                            f"{opening}{t_xml}</{run.qname}>"))
        else:
            splices.append((run.content_end, run.content_end, t_xml))

    # 수정된 문단의 linesegarray 캐시 제거 (stale 캐시 → 한컴 손상 경고 방지)
    affected = {}
    for tn in reg.changed():
        p_el = _nearest(tn.el, "p")
        if p_el is not None:
            affected[id(p_el)] = p_el
    for run, _ in reg.run_insertions:
        p_el = _nearest(run, "p")
        if p_el is not None:
            affected[id(p_el)] = p_el
    for p_el in affected.values():
        for lsa in descendants(p_el, "linesegarray"):
            if ancestor_within(lsa, ("p",), p_el):
                continue  # 중첩 표 내부 등 하위 문단 소속은 건드리지 않음
            splices.append((lsa.start, lsa.end, ""))
    return splices


def apply_splices(xml, splices):
    """겹침 검증 후 splice를 일괄 적용."""
    splices.sort(key=lambda s: s[0])
    for a, b in zip(splices, splices[1:]):
        if a[1] > b[0]:
            raise RuntimeError(f"내부 오류: splice 범위 겹침 {a[:2]} vs {b[:2]}")
    parts, pos = [], 0
    for start, end, repl in splices:
        parts.append(xml[pos:start])
        parts.append(repl)
        pos = end
    parts.append(xml[pos:])
    return "".join(parts)


# ─── 임의 문구 교체 (run 경계를 넘는 텍스트 대응) ──────────────────

def replace_in_section(xml, mapping, counts):
    """문단 단위로 연결한 텍스트에서 옛 문구 → 새 문구 교체.

    한 문구가 여러 <hp:run>/<hp:t>로 쪼개져 있어도(한컴이 자주 그렇게 저장)
    문단 전체를 이어붙여 찾으므로 clone_form.py의 단순 str.replace가
    놓치는 경우를 잡는다. 긴 문구부터 매칭(부분 문자열 오치환 방지).
    """
    root = scan_xml(xml)
    reg = Registry(xml)
    items = sorted(mapping.items(), key=lambda kv: -len(kv[0]))
    changed = False
    for p_el in descendants(root, "p"):
        tnodes = own_tnodes(p_el, reg)
        if not tnodes:
            continue
        full = "".join(tn.text for tn in tnodes)
        for old, new in items:
            if not old or old not in full:
                continue
            idx = full.find(old)
            while idx >= 0:
                replace_text_range(tnodes, idx, idx + len(old), new)
                counts[old] = counts.get(old, 0) + 1
                changed = True
                full = "".join(tn.text for tn in tnodes)
                idx = full.find(old, idx + len(new))
    if not changed:
        return None
    splices = build_splices(xml, reg)
    return apply_splices(xml, splices) if splices else None


# ─── 좌표 기반 셀 채우기 (라벨 매칭 폴백) ──────────────────────────

def fill_cells_in_section(xml, specs, filled, sec_idx):
    """analyze가 보고한 (table, row, col) 좌표로 셀을 직접 채움.

    라벨 휴리스틱이 통하지 않는 복잡한 표에서 결정적 타겟팅을 보장한다.
    table 인덱스는 analyze와 동일하게 문서 순서(중첩 표 포함) 기준.
    """
    root = scan_xml(xml)
    reg = Registry(xml)
    tables = list(descendants(root, "tbl"))
    errors = []
    applied = False
    for spec in specs:
        t, r, c = spec.get("table", 0), spec["row"], spec["col"]
        value = str(spec["value"])
        loc = f"section{sec_idx}.table{t}.row{r}.col{c}"
        if t >= len(tables):
            errors.append(f"{loc}: 표 인덱스 초과 (표 {len(tables)}개)")
            continue
        rows = direct_children(tables[t], "tr")
        if r >= len(rows):
            errors.append(f"{loc}: 행 인덱스 초과 (행 {len(rows)}개)")
            continue
        cells = direct_children(rows[r], "tc")
        if c >= len(cells):
            errors.append(f"{loc}: 열 인덱스 초과 (셀 {len(cells)}개)")
            continue
        replace_cell_text(cells[c], value, reg)
        applied = True
        filled.append({"label": loc, "value": value, "section": sec_idx,
                       "row": r, "col": c, "strategy": "cell-addr"})
    if not applied:
        return None, errors
    splices = build_splices(xml, reg)
    return (apply_splices(xml, splices) if splices else None), errors


# ─── 표 행 추가 (기존 행 복제 — 스타일 100% 보존) ──────────────────

def add_table_rows(xml, table_idx, rows_values, template_row_idx=-1):
    """표의 기존 행 XML을 통째로 복제해 끝에 추가하고 셀 값을 채움.

    행 전체(<hp:tr>...</hp:tr>)를 바이트 그대로 복제하므로 셀 너비·테두리·
    스타일이 완전히 보존된다. 갱신하는 것: 셀 텍스트, cellAddr rowAddr,
    문단 id(고유성), 표의 rowCnt.

    rowSpan 병합이 있는 표는 좌표 체계가 깨질 수 있어 거부한다.
    """
    root = scan_xml(xml)
    tables = list(descendants(root, "tbl"))
    if table_idx >= len(tables):
        raise ValueError(f"표 인덱스 초과: {table_idx} (표 {len(tables)}개)")
    tbl = tables[table_idx]

    # 안전 게이트: rowSpan 병합 표 거부 (graceful)
    for cs in descendants(tbl, "cellSpan"):
        m = re.search(r'rowSpan="(\d+)"', xml[cs.start:cs.open_end])
        if m and int(m.group(1)) != 1:
            raise ValueError("rowSpan 병합이 있는 표는 행 추가 미지원 — "
                             "셀 좌표가 깨질 수 있어 거부합니다")

    trs = direct_children(tbl, "tr")
    if not trs:
        raise ValueError("표에 행이 없습니다")
    template = trs[template_row_idx]
    n_cells = len(direct_children(template, "tc"))
    for i, vals in enumerate(rows_values):
        if len(vals) != n_cells:
            raise ValueError(
                f"rows[{i}] 값 개수({len(vals)})가 셀 수({n_cells})와 다름")

    # 새 행의 rowAddr 시작점 + 문단 id 고유성 확보
    max_row_addr = -1
    for ca in descendants(tbl, "cellAddr"):
        m = re.search(r'rowAddr="(\d+)"', xml[ca.start:ca.open_end])
        if m:
            max_row_addr = max(max_row_addr, int(m.group(1)))
    max_id = 0
    for m in re.finditer(r'\bid="(\d+)"', xml):
        max_id = max(max_id, int(m.group(1)))
    next_pid = [max_id + 1]

    frag = xml[template.start:template.end]
    clones = []
    for i, vals in enumerate(rows_values):
        clones.append(_clone_row(frag, vals, max_row_addr + 1 + i, next_pid))

    splices = []
    last_tr = trs[-1]
    splices.append((last_tr.end, last_tr.end, "".join(clones)))
    m = re.search(r'\browCnt="(\d+)"', xml[tbl.start:tbl.open_end])
    if m:
        new_cnt = int(m.group(1)) + len(rows_values)
        splices.append((tbl.start + m.start(), tbl.start + m.end(),
                        f'rowCnt="{new_cnt}"'))
    return apply_splices(xml, splices)


def _strip_all_linesegarray(scope_el, splices):
    """복제 조각의 linesegarray 전체 제거 — 복제된 캐시는 항상 stale."""
    covered = {(s, e) for s, e, r in splices if r == ""}
    for lsa in descendants(scope_el, "linesegarray"):
        if (lsa.start, lsa.end) not in covered:
            splices.append((lsa.start, lsa.end, ""))


def _clone_row(frag, vals, new_row_addr, next_pid):
    """행 조각 XML 복제 — 셀 값 교체 + rowAddr/문단 id 갱신."""
    root = scan_xml(frag)
    tr = root.children[0]
    reg = Registry(frag)
    for tc, val in zip(direct_children(tr, "tc"), vals):
        if val is not None:
            replace_cell_text(tc, str(val), reg)
    splices = build_splices(frag, reg)
    _strip_all_linesegarray(tr, splices)
    for ca in descendants(tr, "cellAddr"):
        m = re.search(r'\browAddr="\d+"', frag[ca.start:ca.open_end])
        if m:
            splices.append((ca.start + m.start(), ca.start + m.end(),
                            f'rowAddr="{new_row_addr}"'))
    for p_el in descendants(tr, "p"):
        m = re.search(r'\bid="\d+"', frag[p_el.start:p_el.open_end])
        if m:
            splices.append((p_el.start + m.start(), p_el.start + m.end(),
                            f'id="{next_pid[0]}"'))
            next_pid[0] += 1
    return apply_splices(frag, splices)


# ─── 본문 문단 추가 (기존 문단 복제 — 스타일 보존) ─────────────────

def add_paragraphs(xml, specs):
    """기준 문구가 있는 문단 뒤에 새 문단 삽입 (기준 문단 복제).

    paraPrIDRef/charPrIDRef를 복제로 물려받으므로 스타일이 보존된다.
    기준 문단에 secPr(섹션 설정)/표/이미지가 있으면 복제 부적합 — 거부.
    """
    root = scan_xml(xml)
    reg = Registry(xml)
    max_id = 0
    for m in re.finditer(r'\bid="(\d+)"', xml):
        max_id = max(max_id, int(m.group(1)))
    next_pid = [max_id + 1]

    def in_table(el):
        p = el.parent
        while p is not None:
            if p.name == "tbl":
                return True
            p = p.parent
        return False

    splices = []
    for spec in specs:
        after, text = spec["after"], spec["text"]
        target = None
        for p_el in descendants(root, "p"):
            if in_table(p_el):
                continue
            full = "".join(tn.text for tn in own_tnodes(p_el, reg))
            if after in full:
                target = p_el
                break
        if target is None:
            raise ValueError(f"기준 문구를 찾을 수 없음: {after!r}")
        frag = xml[target.start:target.end]
        if re.search(r"<\w+:(secPr|tbl|pic|ole|container)\b", frag):
            raise ValueError(
                f"기준 문단({after!r})에 섹션 설정/표/개체가 포함되어 복제 부적합 — "
                "일반 텍스트 문단을 기준으로 지정하세요")
        splices.append((target.end, target.end,
                        _clone_para(frag, text, next_pid)))
    return apply_splices(xml, splices)


def _clone_para(frag, text, next_pid):
    """문단 조각 복제 — 첫 run에 새 텍스트, 나머지 비움, id 갱신."""
    root = scan_xml(frag)
    p_el = root.children[0]
    reg = Registry(frag)
    runs = list(descendants(p_el, ("run", "r")))
    if runs:
        set_run_text(runs[0], text, reg)
        for r in runs[1:]:
            set_run_text(r, "", reg)
    else:
        ts = [reg.get(t) for t in descendants(p_el, "t")]
        if ts:
            ts[0].text = text
            for tn in ts[1:]:
                tn.text = ""
    splices = build_splices(frag, reg)
    _strip_all_linesegarray(p_el, splices)
    m = re.search(r'\bid="\d+"', frag[p_el.start:p_el.open_end])
    if m:
        splices.append((p_el.start + m.start(), p_el.start + m.end(),
                        f'id="{next_pid[0]}"'))
        next_pid[0] += 1
    return apply_splices(frag, splices)


def add_paras_hwpx(src, dst, specs, section_idx=0):
    """본문 문단 추가 — 기준 문구 뒤에 새 문단 삽입."""
    with open(src, "rb") as f:
        buf = f.read()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        sections = section_names(zf)
        if not sections:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        if section_idx >= len(sections):
            raise ValueError(f"섹션 인덱스 초과: {section_idx}")
        name = sections[section_idx]
        xml = zf.read(name).decode("utf-8")
    new_xml = add_paragraphs(xml, specs)
    out = patch_zip_entries(buf, {name: new_xml.encode("utf-8")})
    with open(dst, "wb") as f:
        f.write(out)
    return name


# ─── ZIP 외과수술 (kordoc zip-patch.ts 포팅) ───────────────────────

EOCD_SIG = b"PK\x05\x06"
CD_SIG = b"PK\x01\x02"
LOCAL_SIG = b"PK\x03\x04"
ZIP64_LOC_SIG = b"PK\x06\x07"


def parse_central_directory(buf):
    """EOCD → Central Directory 파싱. (entries, cd_offset, eocd_offset) 반환."""
    n = len(buf)
    min_eocd = max(0, n - 22 - 65535)
    eocd = -1
    for i in range(n - 22, min_eocd - 1, -1):
        if buf[i:i + 4] == EOCD_SIG and \
                i + 22 + struct.unpack_from("<H", buf, i + 20)[0] == n:
            eocd = i
            break
    if eocd < 0:
        # 폴백: trailing 정크가 붙은 파일 — CD 시그니처가 검증되는 첫 후보
        for i in range(n - 22, min_eocd - 1, -1):
            if buf[i:i + 4] != EOCD_SIG:
                continue
            if i + 22 + struct.unpack_from("<H", buf, i + 20)[0] > n:
                continue
            cand = struct.unpack_from("<I", buf, i + 16)[0]
            if cand < n - 4 and buf[cand:cand + 4] == CD_SIG:
                eocd = i
                break
    if eocd < 0:
        raise ValueError("ZIP EOCD를 찾을 수 없습니다")

    total = struct.unpack_from("<H", buf, eocd + 10)[0]
    cd_offset = struct.unpack_from("<I", buf, eocd + 16)[0]
    if cd_offset == 0xFFFFFFFF or total == 0xFFFF:
        raise ValueError("ZIP64는 지원하지 않습니다")
    if eocd >= 20 and buf[eocd - 20:eocd - 16] == ZIP64_LOC_SIG:
        raise ValueError("ZIP64는 지원하지 않습니다")

    entries = []
    pos = cd_offset
    for _ in range(total):
        if buf[pos:pos + 4] != CD_SIG:
            raise ValueError("ZIP Central Directory 손상")
        flags = struct.unpack_from("<H", buf, pos + 8)[0]
        method = struct.unpack_from("<H", buf, pos + 10)[0]
        comp_size = struct.unpack_from("<I", buf, pos + 20)[0]
        uncomp_size = struct.unpack_from("<I", buf, pos + 24)[0]
        name_len = struct.unpack_from("<H", buf, pos + 28)[0]
        extra_len = struct.unpack_from("<H", buf, pos + 30)[0]
        comment_len = struct.unpack_from("<H", buf, pos + 32)[0]
        local_offset = struct.unpack_from("<I", buf, pos + 42)[0]
        if 0xFFFFFFFF in (comp_size, uncomp_size, local_offset):
            raise ValueError("ZIP64는 지원하지 않습니다")
        name = buf[pos + 46:pos + 46 + name_len].decode("utf-8")
        cd_end = pos + 46 + name_len + extra_len + comment_len
        entries.append({
            "cd_start": pos, "cd_end": cd_end, "name": name, "flags": flags,
            "method": method, "comp_size": comp_size,
            "uncomp_size": uncomp_size, "local_offset": local_offset,
        })
        pos = cd_end
    return entries, cd_offset, eocd


def patch_zip_entries(original, replacements):
    """replacements에 지정된 엔트리만 새 데이터로 교체, 나머지는 바이트 복사.

    엔트리 순서·압축 방식·mimetype 첫 엔트리 규약이 원본 그대로 보존된다.
    """
    entries, cd_offset, eocd_offset = parse_central_directory(original)
    names = {e["name"] for e in entries}
    for name in replacements:
        if name not in names:
            raise ValueError(f"ZIP에 없는 엔트리: {name}")

    by_local = sorted(entries, key=lambda e: e["local_offset"])
    segments = []
    new_local_offset = {}
    new_meta = {}
    offset = 0

    for i, e in enumerate(by_local):
        seg_end = by_local[i + 1]["local_offset"] if i + 1 < len(by_local) else cd_offset
        new_local_offset[e["name"]] = offset
        new_data = replacements.get(e["name"])
        if new_data is None:
            seg = original[e["local_offset"]:seg_end]  # 데이터 디스크립터 포함 원본 그대로
            segments.append(seg)
            offset += len(seg)
            continue

        lo = e["local_offset"]
        if original[lo:lo + 4] != LOCAL_SIG:
            raise ValueError("ZIP 로컬 헤더 시그니처 불일치")
        name_len = struct.unpack_from("<H", original, lo + 26)[0]
        extra_len = struct.unpack_from("<H", original, lo + 28)[0]
        header = bytearray(original[lo:lo + 30 + name_len + extra_len])

        if e["method"] == 0:
            comp_data = new_data
        else:
            c = zlib.compressobj(9, zlib.DEFLATED, -15)  # raw deflate
            comp_data = c.compress(new_data) + c.flush()
        crc = zlib.crc32(new_data) & 0xFFFFFFFF
        flags = e["flags"] & ~0x0008  # 데이터 디스크립터 해제 (사이즈를 헤더에 기록)

        struct.pack_into("<H", header, 6, flags)
        struct.pack_into("<I", header, 14, crc)
        struct.pack_into("<I", header, 18, len(comp_data))
        struct.pack_into("<I", header, 22, len(new_data))
        segments.append(bytes(header))
        segments.append(comp_data)
        offset += len(header) + len(comp_data)
        new_meta[e["name"]] = (flags, crc, len(comp_data), len(new_data))

    # Central Directory — 원본 순서 유지, 오프셋/메타만 패치
    new_cd_offset = offset
    for e in entries:
        cd = bytearray(original[e["cd_start"]:e["cd_end"]])
        struct.pack_into("<I", cd, 42, new_local_offset[e["name"]])
        meta = new_meta.get(e["name"])
        if meta:
            flags, crc, comp_size, uncomp_size = meta
            struct.pack_into("<H", cd, 8, flags)
            struct.pack_into("<I", cd, 16, crc)
            struct.pack_into("<I", cd, 20, comp_size)
            struct.pack_into("<I", cd, 24, uncomp_size)
        segments.append(bytes(cd))
        offset += len(cd)
    new_cd_size = offset - new_cd_offset

    eocd = bytearray(original[eocd_offset:])
    struct.pack_into("<I", eocd, 12, new_cd_size)
    struct.pack_into("<I", eocd, 16, new_cd_offset)
    segments.append(bytes(eocd))
    return b"".join(segments)


# ─── 섹션 파일 탐색 ────────────────────────────────────────────────

_SECTION_RE = re.compile(r"[Ss]ection\d+\.xml$")


def section_names(zf):
    return sorted(n for n in zf.namelist() if _SECTION_RE.search(n))


# ─── analyze: 채울 수 있는 타겟 스캔 ───────────────────────────────

def analyze_hwpx(path):
    with zipfile.ZipFile(path) as zf:
        sections = section_names(zf)
        if not sections:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        targets = {
            "label_value_cells": [],
            "header_tables": [],
            "checkboxes": [],
            "bracket_blanks": [],
            "annotation_blanks": [],
            "inline_labels": [],
        }
        for sec_idx, name in enumerate(sections):
            xml = zf.read(name).decode("utf-8")
            root = scan_xml(xml)
            reg = Registry(xml)
            tables = list(descendants(root, "tbl"))

            seen = set()
            for tbl_idx, tbl in enumerate(tables):
                # 인셀 패턴 후보
                for tc in descendants(tbl, "tc"):
                    if id(tc) in seen or under_tbl_within(tc, tbl):
                        continue
                    seen.add(id(tc))
                    text = "".join(tn.text for tn in reg.cell_tnodes(tc))
                    for m in _CHECKBOX_RE.finditer(text):
                        targets["checkboxes"].append({
                            "key": normalize_label(m.group(1)),
                            "text": m.group(0), "section": sec_idx,
                            "hint": "값을 \"☑\" 또는 \"true\"로 주면 체크됨"})
                    for m in _BRACKET_RE.finditer(text):
                        targets["bracket_blanks"].append({
                            "key": normalize_label(m.group(1) + m.group(2)),
                            "text": m.group(0), "section": sec_idx})
                    for m in _ANNOTATION_RE.finditer(text):
                        targets["annotation_blanks"].append({
                            "key": normalize_label(m.group(1)),
                            "text": m.group(0), "section": sec_idx})

                rows = direct_children(tbl, "tr")
                # 라벨-값 셀 후보
                for row_idx, tr in enumerate(rows):
                    cells = direct_children(tr, "tc")
                    for col_idx in range(len(cells) - 1):
                        label_text = extract_cell_text(cells[col_idx], reg)
                        if not is_label_cell(label_text):
                            continue
                        value_text = extract_cell_text(cells[col_idx + 1], reg)
                        if is_keyword_label(value_text):
                            continue
                        targets["label_value_cells"].append({
                            "key": normalize_label(label_text),
                            "label": label_text.strip(),
                            "current": value_text.strip(),
                            "empty": not value_text.strip(),
                            "section": sec_idx, "table": tbl_idx,
                            "row": row_idx, "col": col_idx,
                        })
                # 헤더 행 테이블 후보
                if len(rows) >= 2:
                    header_cells = direct_children(rows[0], "tc")
                    if header_cells and all(
                            0 < len(extract_cell_text(c, reg).strip()) <= 20
                            and is_label_cell(extract_cell_text(c, reg).strip())
                            for c in header_cells):
                        targets["header_tables"].append({
                            "columns": [extract_cell_text(c, reg).strip()
                                        for c in header_cells],
                            "keys": [normalize_label(extract_cell_text(c, reg))
                                     for c in header_cells],
                            "data_rows": len(rows) - 1,
                            "section": sec_idx, "table": tbl_idx,
                        })

            # 인라인 라벨 후보
            for p_el in descendants(root, "p"):
                parent, in_tbl = p_el.parent, False
                while parent is not None:
                    if parent.name == "tbl":
                        in_tbl = True
                        break
                    parent = parent.parent
                if in_tbl:
                    continue
                full_text = "".join(reg.get(t).text
                                    for t in descendants(p_el, "t")
                                    if not under_tbl_within(t, p_el))
                for m in INLINE_RE.finditer(full_text):
                    targets["inline_labels"].append({
                        "key": normalize_label(m.group(1)),
                        "label": m.group(1),
                        "current": m.group(2).strip(),
                        "section": sec_idx,
                    })

    total = sum(len(v) for v in targets.values())
    return {
        "file": path,
        "sections": sections,
        "target_count": total,
        "targets": targets,
        "usage": ("위 targets의 key를 키로 하는 JSON을 만들어 "
                  "`fill_hwpx.py fill <원본> <출력> --values values.json`을 실행하세요. "
                  "예: {\"성명\": \"홍길동\", \"동의\": \"☑\"}"),
    }


# ─── fill ──────────────────────────────────────────────────────────

def fill_hwpx(src, dst, values=None, cells=None):
    """원본 HWPX의 양식 필드를 채워 dst에 저장.

    values: {라벨: 값} — 라벨 매칭 전략 0~3
    cells:  [{table, row, col, value, section?}] — 좌표 직접 지정 폴백
    """
    with open(src, "rb") as f:
        buf = f.read()
    normalized = normalize_values(values) if values else {}
    matched_labels = set()
    filled = []
    cell_errors = []
    replacements = {}

    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        sections = section_names(zf)
        if not sections:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        for sec_idx, name in enumerate(sections):
            xml = zf.read(name).decode("utf-8")
            cur = xml
            if normalized:
                new_xml = fill_section(cur, normalized, matched_labels,
                                       filled, sec_idx)
                if new_xml is not None:
                    cur = new_xml
            if cells:
                specs = [c for c in cells if c.get("section", 0) == sec_idx]
                if specs:
                    new_xml, errs = fill_cells_in_section(cur, specs,
                                                          filled, sec_idx)
                    cell_errors.extend(errs)
                    if new_xml is not None:
                        cur = new_xml
            if cur != xml:
                replacements[name] = cur.encode("utf-8")

    out = patch_zip_entries(buf, replacements) if replacements else buf
    with open(dst, "wb") as f:
        f.write(out)
    unmatched = resolve_unmatched(normalized, matched_labels, values or {})
    return filled, unmatched, sorted(replacements), cell_errors


def replace_hwpx(src, dst, mapping):
    """문구 교체 (run 경계 무관) — clone_form.py Phase 1의 강화판."""
    with open(src, "rb") as f:
        buf = f.read()
    counts = {old: 0 for old in mapping}
    replacements = {}
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        sections = section_names(zf)
        if not sections:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        for name in sections:
            xml = zf.read(name).decode("utf-8")
            new_xml = replace_in_section(xml, mapping, counts)
            if new_xml is not None and new_xml != xml:
                replacements[name] = new_xml.encode("utf-8")
    out = patch_zip_entries(buf, replacements) if replacements else buf
    with open(dst, "wb") as f:
        f.write(out)
    return counts, sorted(replacements)


def add_rows_hwpx(src, dst, table_idx, rows_values, section_idx=0,
                  template_row_idx=-1):
    """표에 행 추가 (기존 행 복제) — 스타일/너비/테두리 보존."""
    with open(src, "rb") as f:
        buf = f.read()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        sections = section_names(zf)
        if not sections:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        if section_idx >= len(sections):
            raise ValueError(f"섹션 인덱스 초과: {section_idx}")
        name = sections[section_idx]
        xml = zf.read(name).decode("utf-8")
    new_xml = add_table_rows(xml, table_idx, rows_values, template_row_idx)
    out = patch_zip_entries(buf, {name: new_xml.encode("utf-8")})
    with open(dst, "wb") as f:
        f.write(out)
    return name


# ─── 글자 테두리 제거 (hwp2hwpx 변환 버그 보정) ────────────────────

_CHARPR_OPEN_RE = re.compile(r"<(?:\w+:)?charPr\b[^>]*?>")
_BORDERREF_RE = re.compile(r'\s*borderFillIDRef="\d+"')
_CHARPR_REF_RE = re.compile(
    r"<(?:\w+:)?charPr\b[^>]*?borderFillIDRef=\"(\d+)\"")
_BORDER_SOLID_RE = re.compile(
    r"(?:left|right|top|bottom)Border type=\"(?:SOLID|DASH|DOT|THICK"
    r"|DOUBLE|WAVE)\"")


def _borderfill_is_solid(header_xml, bid):
    """header.xml에서 borderFill id=bid가 실제 테두리선을 가지는지."""
    m = re.search(rf'<(?:\w+:)?borderFill\b[^>]*\bid="{bid}"', header_xml)
    if not m:
        return False
    end = header_xml.find("</", m.start())
    # 같은 borderFill 닫는 태그까지
    close = re.search(r"</(?:\w+:)?borderFill>", header_xml[m.start():])
    block = (header_xml[m.start():m.start() + close.end()]
             if close else header_xml[m.start():m.start() + 600])
    return bool(_BORDER_SOLID_RE.search(block))


def detect_char_border_bug(path):
    """글자모양에 테두리가 박힌 변환기 버그인지 탐지.

    hwp2hwpx는 글자모양(charPr) 대다수에 동일한 SOLID 테두리 borderFill을
    참조시킨다(문서 전체 글자에 네모 테두리). 의도적 글자 테두리(일부 charPr만)
    와 구분하기 위해, charPr의 절반 이상이 '실제 테두리선이 있는' borderFill을
    참조할 때만 버그로 판정한다.

    Returns: {"bug": bool, "bordered_charpr": int, "total_charpr": int}
    """
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith("header.xml")]
        if not names:
            return {"bug": False, "bordered_charpr": 0, "total_charpr": 0}
        h = zf.read(names[0]).decode("utf-8")

    total = len(re.findall(r"<(?:\w+:)?charPr\b", h))
    solid_cache = {}
    bordered = 0
    for bid in _CHARPR_REF_RE.findall(h):
        if bid not in solid_cache:
            solid_cache[bid] = _borderfill_is_solid(h, bid)
        if solid_cache[bid]:
            bordered += 1
    bug = total > 0 and bordered >= max(2, total * 0.5)
    return {"bug": bug, "bordered_charpr": bordered, "total_charpr": total}


def strip_char_borders(path, dst=None):
    """글자모양(charPr)에 박힌 글자 테두리 참조(borderFillIDRef)를 제거.

    hwp2hwpx 변환기가 모든 charPr에 테두리 borderFill을 참조시켜 문서의 모든
    글자에 네모 테두리가 생기는 버그를 보정한다. 표 셀(tc)의 borderFillIDRef는
    section에 있어 건드리지 않으므로 표 테두리는 그대로 보존된다.

    Returns: 제거한 참조 수 (0이면 변경 없음).
    """
    with open(path, "rb") as f:
        buf = f.read()
    repl, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        for name in zf.namelist():
            if not name.endswith("header.xml"):
                continue
            h = zf.read(name).decode("utf-8")
            h2 = _CHARPR_OPEN_RE.sub(
                lambda m: _BORDERREF_RE.sub("", m.group(0)), h)
            removed = h.count("borderFillIDRef") - h2.count("borderFillIDRef")
            if removed:
                repl[name] = h2.encode("utf-8")
                total += removed
    if repl:
        out = patch_zip_entries(buf, repl)
        with open(dst or path, "wb") as f:
            f.write(out)
    elif dst and dst != path:
        with open(dst, "wb") as f:
            f.write(buf)
    return total


# ─── 한컴 열림 가능성 사전 점검 (secPr 완전성) ─────────────────────

# secPr 첫 섹션이 반드시 가져야 하는 자식 요소 (없으면 한컴이 '손상 문서'로 판정)
SECPR_REQUIRED = ("pagePr", "margin")
# 있는 게 정상인 권장 요소 (없어도 열릴 수 있으나 경고)
SECPR_RECOMMENDED = ("grid", "startNum", "visibility", "lineNumberShape",
                     "footNotePr")


def _norm_compact(s):
    return re.sub(r"\s", "", s)


def detect_raw_llm(path):
    """한컴을 한 번도 거치지 않은 raw LLM 출력인지 탐지.

    한컴은 저장 시 본문을 기반으로 미리보기(Preview/PrvText.txt)와
    줄배치 캐시(hp:linesegarray)를 항상 생성한다. LLM이 section XML을
    손수 만들어 ZIP으로 묶기만 하면 이 둘이 비어 있고, 한컴이 본문
    레이아웃을 그리지 못해 '빈 페이지'로 열린다(이번 자문수락서 사고).

    Returns: {"raw_suspect": bool, "signals": {...}}
    """
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        body, lsa = "", 0
        for n in section_names(zf):
            x = zf.read(n).decode("utf-8", "replace")
            root = scan_xml(x)
            for t in descendants(root, "t"):
                body += decode_entities(
                    _INNER_TAG_RE.sub("", x[t.content_start:t.content_end]))
            lsa += sum(1 for _ in descendants(root, "linesegarray"))
        prv = (zf.read("Preview/PrvText.txt").decode("utf-8", "replace")
               if "Preview/PrvText.txt" in names else "")

    body_c = _norm_compact(body)
    sample = body_c[:15]
    preview_reflects = bool(sample) and sample in _norm_compact(prv)
    # 본문이 충분히 있는데(>80자) 미리보기 미반영 + 줄배치 전무 → raw 확정 신호
    raw = len(body_c) > 80 and not preview_reflects and lsa == 0
    return {
        "raw_suspect": raw,
        "signals": {
            "body_chars": len(body_c),
            "preview_chars": len(_norm_compact(prv)),
            "linesegarray": lsa,
            "preview_reflects_body": preview_reflects,
        },
    }


def check_openable(path, strict=False):
    """한컴이 문서를 정상으로 열 수 있는지 정적 점검 — XML 유효성 너머.

    두 종류의 사고를 잡는다:
    1) secPr 불완전(pagePr/margin 누락) → '손상된 문서' 복구 대화상자
    2) raw LLM 출력(미리보기/줄배치 부재) → '빈 페이지'로 열림

    validate.py(XML 유효성)·fill verify(값 존재)는 둘 다 못 잡는다.

    strict=True면 raw 의심도 ok=False로 처리(배포 게이트/훅용).

    Returns: {"ok", "errors", "warnings", "raw_llm_suspect", "raw_signals"}
    """
    errors, warnings = [], []
    with zipfile.ZipFile(path) as zf:
        sections = section_names(zf)
        if not sections:
            return {"ok": False, "errors": ["섹션 파일 없음"], "warnings": [],
                    "raw_llm_suspect": False, "raw_signals": {}}
        xml = zf.read(sections[0]).decode("utf-8")

    root = scan_xml(xml)
    secprs = list(descendants(root, "secPr"))
    if not secprs:
        errors.append("첫 섹션에 <hp:secPr>가 없음 — 한컴이 열지 못함")
        secpr = None
    else:
        secpr = secprs[0]
        children = {c.name for c in descendants(secpr, SECPR_REQUIRED + SECPR_RECOMMENDED)}
        for req in SECPR_REQUIRED:
            if req not in children:
                errors.append(
                    f"secPr에 <hp:{req}> 없음 — "
                    f"{'용지 크기' if req == 'pagePr' else '여백'} 미정의로 한컴 열기 실패")
        for rec in SECPR_RECOMMENDED:
            if rec not in children:
                warnings.append(f"secPr에 <hp:{rec}> 없음 (권장 요소)")
        # 가짜 secPr 휴리스틱: secPr 태그에 pageWidth/leftMargin 등 비표준 속성
        open_tag = xml[secpr.start:secpr.open_end]
        bogus = [a for a in ("pageWidth", "pageHeight", "leftMargin",
                             "rightMargin", "topMargin") if a in open_tag]
        if bogus:
            errors.append(
                f"secPr에 비표준 속성 {bogus} — LLM이 손수 작성한 가짜 secPr로 보임. "
                "정상 HWPX의 secPr(pagePr/margin 자식 요소)로 교체 필요")

    # raw LLM 파일(한컴 미경유) 탐지 — '빈 페이지' 사고
    raw = detect_raw_llm(path)
    if raw["raw_suspect"]:
        warnings.append(
            "한컴 미경유 raw 파일 의심 (미리보기·줄배치 부재) — 한컴에서 빈 "
            "페이지로 열릴 수 있음. 정상 HWPX(한컴 저장본/워크플로우 H 변환본)를 "
            "베이스로 fill/replace만 적용하거나, 한컴에서 한 번 열어 저장하세요")

    # 글자 테두리 버그 탐지 — 모든 글자에 네모 테두리
    cb = detect_char_border_bug(path)
    if cb["bug"]:
        warnings.append(
            f"글자 테두리 버그 ({cb['bordered_charpr']}/{cb['total_charpr']} "
            "charPr이 테두리 borderFill 참조) — 모든 글자에 네모 테두리가 보임. "
            "`fill_hwpx.py fix-borders`로 제거하세요")

    ok = (not errors
          and (not strict or (not raw["raw_suspect"] and not cb["bug"])))
    return {"ok": ok, "errors": errors, "warnings": warnings,
            "raw_llm_suspect": raw["raw_suspect"], "raw_signals": raw["signals"],
            "char_border_bug": cb["bug"], "char_border_signals": cb}


# ─── verify ────────────────────────────────────────────────────────

def extract_all_text(path):
    """HWPX 전체 <hp:t> 텍스트 연결 (검증용)."""
    parts = []
    with zipfile.ZipFile(path) as zf:
        for name in section_names(zf):
            xml = zf.read(name).decode("utf-8")
            root = scan_xml(xml)
            for t in descendants(root, "t"):
                raw = xml[t.content_start:t.content_end]
                parts.append(decode_entities(_INNER_TAG_RE.sub("", raw)))
    return "".join(parts)


def verify_hwpx(path, values, original=None):
    """채움 결과 검증 — 값 존재 + (옵션) 비변경 엔트리 바이트 동일성."""
    report = {"file": path, "values": {}, "ok": True}

    # 1) 구조 검증: ZIP + 섹션 XML 파싱 가능
    try:
        full_text = extract_all_text(path)
    except Exception as e:  # noqa: BLE001
        return {"file": path, "ok": False, "error": f"파일 열기 실패: {e}"}

    # 1.5) 한컴 열림 가능성 (secPr 완전성) — '손상 문서' 사고 방지
    openable = check_openable(path)
    report["openable"] = openable
    if not openable["ok"]:
        report["ok"] = False

    # 2) 값 존재 확인
    for key, value in values.items():
        v = value.strip()
        if v in CHECKBOX_TRUTHY:
            found = f"☑{key}" in full_text or "☑" in full_text
            status = "checkbox-checked" if found else "missing"
        else:
            found = v in full_text
            status = "found" if found else "missing"
        report["values"][key] = status
        if not found:
            report["ok"] = False

    # 3) 비변경 엔트리 바이트 동일성 (원본 제공 시)
    if original:
        with open(original, "rb") as f:
            orig_buf = f.read()
        with open(path, "rb") as f:
            out_buf = f.read()
        orig_entries, _, _ = parse_central_directory(orig_buf)
        out_entries, _, _ = parse_central_directory(out_buf)

        def data_of(buf, e):
            lo = e["local_offset"]
            name_len = struct.unpack_from("<H", buf, lo + 26)[0]
            extra_len = struct.unpack_from("<H", buf, lo + 28)[0]
            start = lo + 30 + name_len + extra_len
            return buf[start:start + e["comp_size"]]

        orig_map = {e["name"]: e for e in orig_entries}
        out_map = {e["name"]: e for e in out_entries}
        changed, problems = [], []
        if list(orig_map) != list(out_map):
            problems.append("엔트리 목록/순서가 다름")
        for name in orig_map:
            if name not in out_map:
                continue
            if data_of(orig_buf, orig_map[name]) != data_of(out_buf, out_map[name]):
                changed.append(name)
        non_section_changed = [n for n in changed if not _SECTION_RE.search(n)]
        if non_section_changed:
            problems.append(f"섹션 외 엔트리가 변경됨: {non_section_changed}")
            report["ok"] = False
        report["changed_entries"] = changed
        report["preservation"] = ("섹션 XML만 변경됨 — 원본 보존 OK"
                                  if not problems else problems)

    return report


# ─── 머리말·꼬리말·쪽번호 in-place (claw-hwp hwpx-edit.js 포팅) ───────
#
# 머리말(hp:header)/꼬리말(hp:footer)은 섹션 본문(section*.xml) 안에서
#   <hp:p><hp:run><hp:ctrl><hp:header applyPageType="BOTH">
#     <hp:subList ...><hp:p paraPrIDRef="..">
#       <hp:run charPrIDRef="0"><hp:t>텍스트</hp:t></hp:run></hp:p></hp:subList>
#   </hp:header></hp:ctrl></hp:run></hp:p>
# 형태로 존재한다. secPr를 품은 섹션 첫 문단 '뒤'에 새 문단으로 삽입한다.
# 쪽번호는 머리말/꼬리말 문단 안에 <hp:autoNum numType="PAGE"> 컨트롤을 둔 것
# (한컴이 실제 페이지 번호를 렌더). claw-hwp가 한컴독스 라운드트립으로 검증한
# 봉투(envelope) 구조를 그대로 따른다 — DOM 재직렬화 없이 splice + ZIP 외과수술.

HF_APPLY = {"BOTH", "EVEN", "ODD"}
HF_ALIGN = {"LEFT", "CENTER", "RIGHT"}
_HEADER_XML_RE = re.compile(r"[Hh]eader\.xml$")


def _fresh_para_id(xml):
    """섹션에서 아직 쓰이지 않은 최소 양의 정수 id (32비트 초과·충돌 방지).

    실제 한컴 저장본의 문단 id는 2764991984 같은 거대 난수라 max+1은
    오버플로 위험이 있다 — 미사용 최소값을 쓰면 항상 안전한 범위에 든다.
    """
    used = set(re.findall(r'\bid="(\d+)"', xml))
    i = 1
    while str(i) in used:
        i += 1
    return i


def _aligned_parapr_id(header_xml, align):
    """header.xml에서 요청한 가로 정렬을 이미 선언한 <hh:paraPr> id를 찾는다.

    없으면 None — 호출부가 기본 paraPrIDRef='0'으로 폴백한다. header.xml에
    새 paraPr를 주입하지 않는 best-effort 방식(주입 시 itemCnt 보정 실수가
    '한컴 손상 문서' 사고로 이어지므로 회피). 정부 양식은 대개 CENTER/RIGHT
    paraPr를 이미 보유해 충분히 동작한다.
    """
    if not header_xml:
        return None
    pat = re.compile(r'<hh:align\b[^>]*horizontal="%s"' % align)
    root = scan_xml(header_xml)
    for pp in descendants(root, "paraPr"):
        if pat.search(header_xml[pp.content_start:pp.content_end]):
            m = re.search(r'\bid="(\d+)"', header_xml[pp.start:pp.open_end])
            if m:
                return m.group(1)
    return None


def _resolve_align(header_xml, align):
    """(paraPrIDRef, note) 반환. align 미지정/LEFT면 기본 '0'."""
    if not align:
        return "0", "default"
    al = align.upper()
    if al not in HF_ALIGN:
        raise ValueError("align은 LEFT/CENTER/RIGHT 중 하나여야 합니다")
    if al == "LEFT":
        return "0", "LEFT(기본)"
    found = _aligned_parapr_id(header_xml, al)
    if found:
        return found, al
    return "0", "%s 요청했으나 header.xml에 일치 paraPr 없음 → 기본 정렬" % al


def _hf_text_run(text):
    return '<hp:run charPrIDRef="0"><hp:t>%s</hp:t></hp:run>' % escape_text(text)


def _pagenum_run():
    return ('<hp:run charPrIDRef="0"><hp:ctrl>'
            '<hp:autoNum num="1" numType="PAGE">'
            '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" '
            'suffixChar="" supscript="0"/></hp:autoNum></hp:ctrl>'
            '<hp:t/></hp:run>')


def _hf_element(kind, apply, ppr, inner_runs):
    """<hp:header>/<hp:footer> 요소 자체 (subList>p>runs)."""
    vert = "TOP" if kind == "header" else "BOTTOM"
    return (
        '<hp:%s id="0" applyPageType="%s">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="%s" linkListIDRef="0" linkListNextIDRef="0" '
        'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        '<hp:p id="0" paraPrIDRef="%s" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0">%s</hp:p>'
        '</hp:subList></hp:%s>' % (kind, apply, vert, ppr, inner_runs, kind))


def _hf_wrapper(xml, kind, apply, ppr, inner_runs):
    """머리말/꼬리말 컨트롤을 담을 새 본문 문단 (한컴이 줄배치를 재생성하므로
    linesegarray 없이 둔다)."""
    return (
        '<hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:ctrl>%s'
        '</hp:ctrl></hp:run></hp:p>'
        % (_fresh_para_id(xml), _hf_element(kind, apply, ppr, inner_runs)))


def _body_paragraphs(root):
    """섹션 컨테이너(<hs:sec>)의 최상위 <hp:p> 목록 + 컨테이너 요소."""
    for top in root.children:
        ps = direct_children(top, "p")
        if ps:
            return top, ps
    return root, direct_children(root, "p")


def _find_hf(root, kind):
    """ctrl 안에 든 페이지 머리말/꼬리말 요소 목록 (문서 순서).

    표 셀의 header="1" 속성 등과 혼동하지 않도록 부모가 <hp:ctrl>인 것만.
    """
    return [e for e in descendants(root, kind)
            if e.parent is not None and e.parent.name == "ctrl"]


def _load_doc(src):
    with open(src, "rb") as f:
        buf = f.read()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = section_names(zf)
        if not names:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        xmls = {n: zf.read(n).decode("utf-8") for n in names}
        header_xml = next((zf.read(h).decode("utf-8")
                           for h in zf.namelist() if _HEADER_XML_RE.search(h)),
                          None)
    return buf, names, xmls, header_xml


def _write_doc(src_buf, dst, changed):
    if changed:
        out = patch_zip_entries(src_buf, {n: x.encode("utf-8")
                                          for n, x in changed.items()})
    else:
        out = src_buf  # 변경 없음 — 원본 바이트 그대로 복사
    with open(dst, "wb") as f:
        f.write(out)


def set_header_footer_hwpx(src, dst, kind, text, apply=None, align=None):
    """머리말/꼬리말 삽입 또는 갱신.

    기존 머리말/꼬리말이 있으면 (한 문서에 여러 슬롯이 있어도) **전부** 같은
    텍스트로 갱신한다 — 정부 양식은 머리말 슬롯을 2개 두기도 해서, 첫 개만
    채우면 한컴이 다른 슬롯을 골라 일부 페이지에 안 보이는 사고가 난다.
    apply=None이면 각 슬롯의 기존 applyPageType를 보존하고, align 미지정이면
    기존 정렬을 보존한다(텍스트만 교체). 헤더 내부는 셀과 동형(subList>p>run>t)
    이라 replace_cell_text를 재사용하며, 수정 문단의 stale linesegarray는
    build_splices가 자동 제거한다.
    """
    if apply is not None:
        apply = apply.upper()
        if apply not in HF_APPLY:
            raise ValueError("applyPageType는 BOTH/EVEN/ODD 중 하나여야 합니다")
    buf, names, xmls, header_xml = _load_doc(src)
    ppr, note = _resolve_align(header_xml, align)
    changed = {}
    updated = 0
    for n in names:
        xml = xmls[n]
        root = scan_xml(xml)
        reg = Registry(xml)
        els = _find_hf(root, kind)
        if not els:
            continue
        for el in els:
            replace_cell_text(el, text, reg)
        splices = build_splices(xml, reg)
        for el in els:
            if apply is not None:
                open_tag = xml[el.start:el.open_end]
                new_open = (re.sub(r'applyPageType="[^"]*"',
                                   'applyPageType="%s"' % apply, open_tag)
                            if 'applyPageType="' in open_tag
                            else open_tag[:-1] + ' applyPageType="%s">' % apply)
                if new_open != open_tag:
                    splices.append((el.start, el.open_end, new_open))
            if align:
                inner_p = next(iter(descendants(el, "p")), None)
                if inner_p is not None:
                    m = re.search(r'paraPrIDRef="\d+"',
                                  xml[inner_p.start:inner_p.open_end])
                    if m:
                        splices.append((inner_p.start + m.start(),
                                        inner_p.start + m.end(),
                                        'paraPrIDRef="%s"' % ppr))
        changed[n] = apply_splices(xml, splices)
        updated += len(els)
    if updated:
        _write_doc(buf, dst, changed)
        return {"action": "updated", "instances": updated, "kind": kind,
                "applyPageType": apply or "preserved",
                "align": (note if align else "preserved"), "text": text}
    # 없으면 첫 섹션 secPr 문단 뒤에 삽입
    n = names[0]
    xml = xmls[n]
    _, paras = _body_paragraphs(scan_xml(xml))
    if not paras:
        raise ValueError("첫 섹션에 본문 문단(<hp:p>)이 없어 삽입 위치를 찾지 못함")
    at = paras[0].end
    new_xml = (xml[:at]
               + _hf_wrapper(xml, kind, apply or "BOTH", ppr, _hf_text_run(text))
               + xml[at:])
    _write_doc(buf, dst, {n: new_xml})
    return {"action": "inserted", "section": n, "kind": kind,
            "applyPageType": apply or "BOTH", "align": note, "text": text}


def set_pagenum_hwpx(src, dst, where="footer", align="CENTER"):
    """자동 쪽번호 삽입. 같은 종류 머리말/꼬리말이 있으면 그 안에 번호를 추가,
    없으면 쪽번호 전용 머리말/꼬리말을 새로 만든다(중복 꼬리말 방지)."""
    kind = "header" if str(where).lower() == "header" else "footer"
    buf, names, xmls, header_xml = _load_doc(src)
    ppr, note = _resolve_align(header_xml, align)
    run = _pagenum_run()
    # 1) 기존 머리말/꼬리말(여러 개여도 전부) 첫 문단에 번호 run 추가
    changed = {}
    added = 0
    for n in names:
        xml = xmls[n]
        els = _find_hf(scan_xml(xml), kind)
        if not els:
            continue
        splices = []
        for el in els:
            first_p = next(iter(descendants(el, "p")), None)
            if first_p is None:
                continue
            # 번호 run 삽입 + 이 문단의 stale linesegarray 제거(한컴 손상 경고 방지)
            splices.append((first_p.content_end, first_p.content_end, run))
            for lsa in descendants(first_p, "linesegarray"):
                if not ancestor_within(lsa, ("p",), first_p):
                    splices.append((lsa.start, lsa.end, ""))
            added += 1
        if splices:
            changed[n] = apply_splices(xml, splices)
    if added:
        _write_doc(buf, dst, changed)
        return {"action": "added-to-existing", "instances": added,
                "where": kind, "align": "기존 %s 정렬 따름" % kind}
    # 2) 쪽번호 전용 머리말/꼬리말 삽입
    n = names[0]
    xml = xmls[n]
    _, paras = _body_paragraphs(scan_xml(xml))
    if not paras:
        raise ValueError("첫 섹션에 본문 문단(<hp:p>)이 없어 삽입 위치를 찾지 못함")
    at = paras[0].end
    new_xml = xml[:at] + _hf_wrapper(xml, kind, "BOTH", ppr, run) + xml[at:]
    _write_doc(buf, dst, {n: new_xml})
    return {"action": "inserted", "section": n, "where": kind, "align": note}


def remove_header_footer_hwpx(src, dst, kind):
    """머리말/꼬리말 제거. 컨트롤이 해당 문단의 유일한 run이면 문단째,
    아니면 컨트롤을 품은 <hp:run>만 삭제(secPr 등 다른 내용 보존)."""
    buf, names, xmls, _ = _load_doc(src)
    changed = {}
    total = 0
    for n in names:
        xml = xmls[n]
        if "<hp:%s" % kind not in xml:
            continue
        cuts = []
        for el in _find_hf(scan_xml(xml), kind):
            run = el.parent.parent if el.parent and el.parent.parent else None
            if run is None or run.name != "run":
                continue
            wp = run.parent
            if (wp is not None and wp.name == "p"
                    and len(direct_children(wp, "run")) == 1):
                cuts.append((wp.start, wp.end))      # 머리말 전용 문단 통째 제거
            else:
                cuts.append((run.start, run.end))    # run만 제거 (다른 내용 보존)
        if not cuts:
            continue
        for a, b in sorted(cuts, reverse=True):
            xml = xml[:a] + xml[b:]
            total += 1
        changed[n] = xml
    _write_doc(buf, dst, changed)
    return {"action": "removed", "kind": kind, "removed": total,
            "sections": list(changed)}


# ─── CLI ───────────────────────────────────────────────────────────

def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="HWPX 원본 보존 채우기 (analyze → fill → verify)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="채울 수 있는 타겟을 JSON으로 출력")
    p_an.add_argument("input")

    p_fill = sub.add_parser("fill", help="values JSON으로 양식 채우기")
    p_fill.add_argument("input")
    p_fill.add_argument("output")
    p_fill.add_argument("--values",
                        help='{"라벨": "값"} JSON 파일 경로 (또는 - 로 stdin)')
    p_fill.add_argument("--cells",
                        help='[{"table","row","col","value"}] 좌표 지정 JSON')
    p_fill.add_argument("--report", help="결과 리포트 JSON 저장 경로")

    p_rep = sub.add_parser("replace",
                           help="문구 교체 — run 경계를 넘는 텍스트도 잡음")
    p_rep.add_argument("input")
    p_rep.add_argument("output")
    p_rep.add_argument("--map", required=True,
                       help='{"옛 문구": "새 문구"} JSON 파일 경로')

    p_row = sub.add_parser("add-row", help="표에 행 추가 (기존 행 복제)")
    p_row.add_argument("input")
    p_row.add_argument("output")
    p_row.add_argument("--table", type=int, required=True,
                       help="표 인덱스 (analyze의 table 번호)")
    p_row.add_argument("--rows", required=True,
                       help='[["셀1","셀2",...], ...] JSON 파일 경로')
    p_row.add_argument("--section", type=int, default=0)
    p_row.add_argument("--template-row", type=int, default=-1,
                       help="복제할 행 인덱스 (기본: 마지막 행)")

    p_par = sub.add_parser("add-para",
                           help="본문 문단 추가 (기준 문구 뒤에 삽입)")
    p_par.add_argument("input")
    p_par.add_argument("output")
    p_par.add_argument("--after", help="기준 문구 (이 문구가 있는 문단 뒤에 삽입)")
    p_par.add_argument("--text", help="추가할 문단 텍스트")
    p_par.add_argument("--paras",
                       help='[{"after","text"}] 배치 JSON 파일 경로')
    p_par.add_argument("--section", type=int, default=0)

    p_ver = sub.add_parser("verify", help="채움 결과 검증")
    p_ver.add_argument("input")
    p_ver.add_argument("--values", required=True)
    p_ver.add_argument("--original", help="원본 파일 — 비변경 엔트리 바이트 비교")

    p_chk = sub.add_parser("check",
                           help="한컴 열림 가능성 점검 (secPr + raw 파일, 값 불필요)")
    p_chk.add_argument("input")
    p_chk.add_argument("--strict", action="store_true",
                       help="raw LLM 파일(빈 페이지 위험)도 실패(exit 2)로 처리")

    p_fb = sub.add_parser("fix-borders",
                          help="글자 테두리 제거 (hwp2hwpx 변환 버그 보정)")
    p_fb.add_argument("input")
    p_fb.add_argument("output", nargs="?",
                      help="출력 경로 (생략 시 입력 파일 덮어쓰기)")

    for _cmd, _h in (("set-header", "머리말 삽입/갱신"),
                     ("set-footer", "꼬리말 삽입/갱신")):
        _p = sub.add_parser(_cmd, help=_h)
        _p.add_argument("input")
        _p.add_argument("output")
        _p.add_argument("--text", required=True, help="머리말/꼬리말 텍스트")
        _p.add_argument("--apply",
                        help="적용 페이지 BOTH/EVEN/ODD "
                             "(미지정 시 기존값 보존, 신규는 BOTH)")
        _p.add_argument("--align",
                        help="가로 정렬 LEFT/CENTER/RIGHT (best-effort)")

    p_pn = sub.add_parser("set-pagenum", help="자동 쪽번호 삽입")
    p_pn.add_argument("input")
    p_pn.add_argument("output")
    p_pn.add_argument("--where", default="footer", choices=["header", "footer"],
                      help="쪽번호 위치 (기본 footer)")
    p_pn.add_argument("--align", default="CENTER",
                      help="가로 정렬 LEFT/CENTER/RIGHT (기본 CENTER)")

    for _cmd, _h in (("remove-header", "머리말 제거"),
                     ("remove-footer", "꼬리말 제거")):
        _p = sub.add_parser(_cmd, help=_h)
        _p.add_argument("input")
        _p.add_argument("output")

    args = parser.parse_args()

    def load_values(spec):
        if spec == "-":
            return json.load(sys.stdin)
        with open(spec, encoding="utf-8") as f:
            return json.load(f)

    try:
        if args.command == "analyze":
            _print(analyze_hwpx(args.input))
            return 0

        if args.command == "fill":
            if not args.values and not args.cells:
                print("오류: --values 또는 --cells 중 하나는 필요합니다",
                      file=sys.stderr)
                return 1
            values = load_values(args.values) if args.values else None
            cells = load_values(args.cells) if args.cells else None
            if values is not None and (not isinstance(values, dict) or not values):
                print("오류: values는 비어있지 않은 JSON 객체여야 합니다",
                      file=sys.stderr)
                return 1
            if cells is not None and not isinstance(cells, list):
                print("오류: cells는 JSON 배열이어야 합니다", file=sys.stderr)
                return 1
            filled, unmatched, modified, cell_errors = fill_hwpx(
                args.input, args.output, values, cells)
            report = {
                "input": args.input, "output": args.output,
                "filled": filled, "unmatched": unmatched,
                "cell_errors": cell_errors,
                "modified_entries": modified,
                "ok": bool(filled) and not cell_errors,
            }
            if args.report:
                with open(args.report, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
            _print(report)
            return 0 if report["ok"] else 2

        if args.command == "replace":
            mapping = load_values(args.map)
            if not isinstance(mapping, dict) or not mapping:
                print("오류: map은 비어있지 않은 JSON 객체여야 합니다",
                      file=sys.stderr)
                return 1
            counts, modified = replace_hwpx(args.input, args.output, mapping)
            total = sum(counts.values())
            _print({"input": args.input, "output": args.output,
                    "replaced": counts, "total": total,
                    "not_found": [k for k, v in counts.items() if v == 0],
                    "modified_entries": modified, "ok": total > 0})
            return 0 if total > 0 else 2

        if args.command == "add-row":
            rows_values = load_values(args.rows)
            if not isinstance(rows_values, list) or not rows_values:
                print("오류: rows는 비어있지 않은 JSON 배열이어야 합니다",
                      file=sys.stderr)
                return 1
            entry = add_rows_hwpx(args.input, args.output, args.table,
                                  rows_values, args.section,
                                  args.template_row)
            _print({"input": args.input, "output": args.output,
                    "table": args.table, "rows_added": len(rows_values),
                    "modified_entries": [entry], "ok": True})
            return 0

        if args.command == "add-para":
            if args.paras:
                specs = load_values(args.paras)
            elif args.after and args.text:
                specs = [{"after": args.after, "text": args.text}]
            else:
                print("오류: --after/--text 또는 --paras가 필요합니다",
                      file=sys.stderr)
                return 1
            entry = add_paras_hwpx(args.input, args.output, specs,
                                   args.section)
            _print({"input": args.input, "output": args.output,
                    "paras_added": len(specs),
                    "modified_entries": [entry], "ok": True})
            return 0

        if args.command == "verify":
            values = load_values(args.values)
            report = verify_hwpx(args.input, values, args.original)
            _print(report)
            return 0 if report.get("ok") else 2

        if args.command == "check":
            report = check_openable(args.input, strict=args.strict)
            report["file"] = args.input
            _print(report)
            return 0 if report["ok"] else 2

        if args.command == "fix-borders":
            removed = strip_char_borders(args.input, args.output)
            _print({"input": args.input,
                    "output": args.output or args.input,
                    "char_borders_removed": removed,
                    "ok": True})
            return 0

        if args.command in ("set-header", "set-footer"):
            kind = "header" if args.command == "set-header" else "footer"
            info = set_header_footer_hwpx(args.input, args.output, kind,
                                          args.text, args.apply, args.align)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "set-pagenum":
            info = set_pagenum_hwpx(args.input, args.output,
                                    args.where, args.align)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command in ("remove-header", "remove-footer"):
            kind = "header" if args.command == "remove-header" else "footer"
            info = remove_header_footer_hwpx(args.input, args.output, kind)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
