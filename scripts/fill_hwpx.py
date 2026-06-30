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


def add_zip_entries(original, additions):
    """기존 엔트리는 바이트 그대로 두고 새 엔트리만 끝에 추가.

    patch_zip_entries가 '존재하는 엔트리만 교체'라면, 이쪽은 '새 엔트리만
    추가'다 (차트 파트 Chart/chartN.xml 등). 기존 로컬 영역과 Central
    Directory 레코드를 한 바이트도 옮기지 않으므로(새 엔트리는 CD 뒤가 아니라
    기존 로컬 영역 끝에 덧붙이고 CD를 그만큼 뒤로 민다) 원본 보존이 유지된다.
    """
    if not additions:
        return original
    entries, cd_offset, eocd_offset = parse_central_directory(original)
    existing = {e["name"] for e in entries}
    for name in additions:
        if name in existing:
            raise ValueError(f"이미 존재하는 엔트리: {name}")

    out = bytearray()
    out += original[:cd_offset]  # 기존 로컬 영역 그대로
    new_cd = []
    for name, data in additions.items():
        name_b = name.encode("utf-8")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        c = zlib.compressobj(9, zlib.DEFLATED, -15)  # raw deflate
        comp = c.compress(data) + c.flush()
        local_off = len(out)
        lh = bytearray(30)
        lh[0:4] = LOCAL_SIG
        struct.pack_into("<H", lh, 4, 20)     # version needed
        struct.pack_into("<H", lh, 6, 0)      # flags
        struct.pack_into("<H", lh, 8, 8)      # method: deflate
        struct.pack_into("<H", lh, 10, 0)     # mod time
        struct.pack_into("<H", lh, 12, 0x21)  # mod date 1980-01-01
        struct.pack_into("<I", lh, 14, crc)
        struct.pack_into("<I", lh, 18, len(comp))
        struct.pack_into("<I", lh, 22, len(data))
        struct.pack_into("<H", lh, 26, len(name_b))
        struct.pack_into("<H", lh, 28, 0)     # extra len
        out += bytes(lh) + name_b + comp
        cd = bytearray(46)
        cd[0:4] = CD_SIG
        struct.pack_into("<H", cd, 4, 20)     # version made by
        struct.pack_into("<H", cd, 6, 20)     # version needed
        struct.pack_into("<H", cd, 8, 0)      # flags
        struct.pack_into("<H", cd, 10, 8)     # method
        struct.pack_into("<H", cd, 12, 0)     # time
        struct.pack_into("<H", cd, 14, 0x21)  # date
        struct.pack_into("<I", cd, 16, crc)
        struct.pack_into("<I", cd, 20, len(comp))
        struct.pack_into("<I", cd, 24, len(data))
        struct.pack_into("<H", cd, 28, len(name_b))
        struct.pack_into("<H", cd, 30, 0)     # extra len
        struct.pack_into("<H", cd, 32, 0)     # comment len
        struct.pack_into("<H", cd, 34, 0)     # disk number
        struct.pack_into("<H", cd, 36, 0)     # internal attrs
        struct.pack_into("<I", cd, 38, 0)     # external attrs
        struct.pack_into("<I", cd, 42, local_off)
        new_cd.append(bytes(cd) + name_b)

    new_cd_offset = len(out)
    out += original[cd_offset:eocd_offset]  # 기존 CD 레코드 (오프셋 불변)
    for rec in new_cd:
        out += rec
    new_cd_size = len(out) - new_cd_offset

    eocd = bytearray(original[eocd_offset:])
    total = struct.unpack_from("<H", eocd, 10)[0] + len(additions)
    struct.pack_into("<H", eocd, 8, total)    # 이 디스크의 엔트리 수
    struct.pack_into("<H", eocd, 10, total)   # 전체 엔트리 수
    struct.pack_into("<I", eocd, 12, new_cd_size)
    struct.pack_into("<I", eocd, 16, new_cd_offset)
    out += bytes(eocd)
    return bytes(out)


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


def detect_vertical_dominance(path):
    """셀 textDirection이 대부분 VERTICAL이면 hwp2hwpx 세로쓰기 오변환 의심.

    가로 양식이 변환기 버그로 세로로 뒤집혀 '글자가 세로로' 깨져 보이는 사고를
    잡는다(강사카드 사례). raw 탐지·XML 유효성은 못 잡는 시각적 깨짐이다.
    세로가 소수면 의도적 세로 셀일 수 있어 의심하지 않는다.
    """
    v = h = 0
    with zipfile.ZipFile(path) as zf:
        for n in section_names(zf):
            x = zf.read(n).decode("utf-8", "replace")
            v += x.count('textDirection="VERTICAL"')
            h += x.count('textDirection="HORIZONTAL"')
    return {"suspect": v >= 3 and v > h, "vertical": v, "horizontal": h}


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

    # 세로쓰기 오변환 탐지 — hwp2hwpx가 가로 양식을 세로로 만든 사고
    vd = detect_vertical_dominance(path)
    if vd["suspect"]:
        warnings.append(
            f"세로쓰기 오변환 의심 (셀 textDirection VERTICAL {vd['vertical']} > "
            f"HORIZONTAL {vd['horizontal']}) — 글자가 세로로 뒤집혀 보일 수 있음. "
            "convert_hwp.py로 재변환(자동 보정 포함)하거나 VERTICAL→HORIZONTAL 교정")

    ok = (not errors
          and (not strict or (not raw["raw_suspect"] and not cb["bug"]
                              and not vd["suspect"])))
    return {"ok": ok, "errors": errors, "warnings": warnings,
            "raw_llm_suspect": raw["raw_suspect"], "raw_signals": raw["signals"],
            "char_border_bug": cb["bug"], "char_border_signals": cb,
            "vertical_misconvert": vd["suspect"], "vertical_signals": vd}


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


# ─── 네이티브 수식 삽입 (claw-hwp hwpx-edit.js buildEquationXml 포팅) ──
#
# 수식은 자기완결 <hp:equation> 봉투다 — font 속성을 객체에 내장하므로
# header.xml charPr/BinData/매니페스트 등록이 전혀 필요 없다(외부 의존 0).
# <hp:script>의 수식 문자열은 한컴 수식 편집기 문법(references/equation-syntax.md)
# 이며 escape_text로 이스케이프해 그대로 삽입한다. <hp:sz>는 한컴이 script로
# 재계산하는 렌더 힌트. treatAsChar="1"로 인라인 배치(글자처럼 흐름).
# claw가 한컴독스 라운드트립으로 검증한 봉투 구조/속성을 임의 변형 없이 따른다.

EQUATION_DEFAULT_W = 9200   # <hp:sz> 폭 힌트 (한컴이 script로 재계산)
EQUATION_DEFAULT_H = 2588   # <hp:sz> 높이 힌트
EQUATION_DEFAULT_BASE_UNIT = 1000  # 폰트 크기 (1000 ≈ 10pt)


def _fresh_ids(xml, count):
    """섹션에서 아직 쓰이지 않은 최소 양의 정수 id를 count개 (오름차순).

    실제 한컴 저장본 id는 거대 난수라 max+1은 32비트 오버플로 위험이 있다 —
    미사용 최소값을 쓰면 항상 안전한 범위에 들고 문서 내 유일성도 보장된다.
    """
    used = set(re.findall(r'\bid="(\d+)"', xml))
    ids, i = [], 1
    while len(ids) < count:
        if str(i) not in used:
            ids.append(i)
        i += 1
    return ids


def _equation_xml(script, eq_id, width=EQUATION_DEFAULT_W,
                  height=EQUATION_DEFAULT_H,
                  base_unit=EQUATION_DEFAULT_BASE_UNIT):
    """인라인 <hp:equation> 봉투 (claw buildEquationXml 1:1 포팅).

    eq_id는 문서 내 유일한 정수 id. script는 escape_text로 이스케이프된다.
    """
    return (
        '<hp:equation id="%d" zOrder="0" numberingType="EQUATION" '
        'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
        'dropcapstyle="None" version="Equation Version 60" baseLine="71" '
        'textColor="#000000" baseUnit="%d" lineMode="CHAR" font="HancomEQN">'
        '<hp:sz width="%d" widthRelTo="ABSOLUTE" height="%d" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" '
        'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
        'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
        '<hp:script>%s</hp:script></hp:equation>'
        % (eq_id, base_unit, width, height, escape_text(script))
    )


def _para_not_in_table(el):
    p = el.parent
    while p is not None:
        if p.name == "tbl":
            return False
        p = p.parent
    return True


def add_equation_after(xml, after, script, width, height, base_unit):
    """기준 문구가 든 본문 문단 뒤에 수식 전용 새 문단을 삽입.

    수식은 자체 PLAIN 문단(paraPrIDRef="0")에 둔다 — 앵커 문단이 목록/번호
    머리(▶, "3.")를 가져도 그것을 물려받지 않게(claw opInsertEquation과 동일).
    charPrIDRef만 앵커에서 빌려 폰트 정합을 맞춘다.
    """
    root = scan_xml(xml)
    reg = Registry(xml)
    target = None
    for p_el in descendants(root, "p"):
        if not _para_not_in_table(p_el):
            continue
        full = "".join(tn.text for tn in own_tnodes(p_el, reg))
        if after in full:
            target = p_el
            break
    if target is None:
        raise ValueError("기준 문구를 찾을 수 없음: %r" % after)
    ids = _fresh_ids(xml, 2)
    eq = _equation_xml(script, ids[1], width, height, base_unit)
    m = re.search(r'charPrIDRef="(\d+)"', xml[target.start:target.end])
    char_id = m.group(1) if m else "0"
    new_para = (
        '<hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0"><hp:run charPrIDRef="%s">%s</hp:run></hp:p>'
        % (ids[0], char_id, eq))
    return apply_splices(xml, [(target.end, target.end, new_para)])


def add_equation_in_cell(xml, table, row, col, script, width, height,
                         base_unit):
    """표 셀(table/row/col)의 첫 문단 끝에 인라인 수식 run을 추가.

    좌표 모델은 fill --cells와 동일: table은 문서순서(중첩표 포함), row/col은
    cellAddr. 셀의 첫 문단 charPrIDRef를 빌려 새 <hp:run>으로 감싸 삽입한다
    (claw placeObjectInCell과 동일 — 기존 문단/내용 보존).
    """
    root = scan_xml(xml)
    tables = list(descendants(root, "tbl"))
    if table >= len(tables):
        raise ValueError("표 인덱스 초과: %d (표 %d개)" % (table, len(tables)))
    rows = direct_children(tables[table], "tr")
    if row >= len(rows):
        raise ValueError("행 인덱스 초과: %d (행 %d개)" % (row, len(rows)))
    cells = direct_children(rows[row], "tc")
    if col >= len(cells):
        raise ValueError("열 인덱스 초과: %d (셀 %d개)" % (col, len(cells)))
    paras = cell_paragraphs(cells[col])
    if not paras:
        raise ValueError("셀에 문단(<hp:p>)이 없어 수식을 삽입할 위치가 없음")
    first_p = paras[0]
    eq = _equation_xml(script, _fresh_ids(xml, 1)[0], width, height, base_unit)
    m = re.search(r'charPrIDRef="(\d+)"',
                  xml[first_p.content_start:first_p.content_end])
    char_id = m.group(1) if m else "0"
    run = '<hp:run charPrIDRef="%s">%s</hp:run>' % (char_id, eq)
    return apply_splices(xml, [(first_p.content_end, first_p.content_end, run)])


def add_equation_hwpx(src, dst, script, after=None, table=None, row=None,
                      col=None, width=EQUATION_DEFAULT_W,
                      height=EQUATION_DEFAULT_H,
                      base_unit=EQUATION_DEFAULT_BASE_UNIT, section_idx=0):
    """본문(--after) 또는 셀(--table/--row/--col)에 네이티브 수식 삽입."""
    if not script or not str(script).strip():
        raise ValueError("--script(수식 문자열)가 필요합니다")
    if base_unit <= 0 or width <= 0 or height <= 0:
        raise ValueError("--size 등 크기 값은 양의 정수여야 합니다")
    in_cell = table is not None or row is not None or col is not None
    if after is None and not in_cell:
        raise ValueError("--after 또는 --table/--row/--col 중 하나를 지정하세요")
    if after is not None and in_cell:
        raise ValueError("--after와 --table/--row/--col은 함께 쓸 수 없습니다")
    if in_cell and (table is None or row is None or col is None):
        raise ValueError("셀 지정에는 --table/--row/--col 모두 필요합니다")
    with open(src, "rb") as f:
        buf = f.read()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        sections = section_names(zf)
        if not sections:
            raise ValueError("HWPX에서 섹션 파일을 찾을 수 없습니다")
        if section_idx >= len(sections):
            raise ValueError("섹션 인덱스 초과: %d" % section_idx)
        name = sections[section_idx]
        xml = zf.read(name).decode("utf-8")
    if after is not None:
        new_xml = add_equation_after(xml, after, str(script),
                                     width, height, base_unit)
        where = {"mode": "after", "after": after}
    else:
        new_xml = add_equation_in_cell(xml, table, row, col, str(script),
                                       width, height, base_unit)
        where = {"mode": "cell", "table": table, "row": row, "col": col}
    out = patch_zip_entries(buf, {name: new_xml.encode("utf-8")})
    with open(dst, "wb") as f:
        f.write(out)
    return name, where


# ─── 직인/서명·이미지 삽입 (claw-hwp place_seal/opInsertImage 포팅) ──────
#
# 사용자 제공 PNG/JPG를 BinData/에 추가하고 content.hpf 매니페스트에 등록한 뒤,
# section XML에 <hp:pic> 봉투를 삽입한다. 두 가지 배치:
#   place-seal  — 기준 문구(발신명의·"서명 또는 인") 옆에 **떠있는(floating)** 그림.
#                 treatAsChar="0" flowWithText="0"라 셀/표/페이지를 키우지 않고
#                 글자 위에 겹쳐 찍힌다(claw opPlaceSeal의 핵심 속성).
#   insert-image — 일반 이미지. 기본은 새 문단(블록, 가운데)으로, --inline이면
#                  기준 문단 끝에 글자처럼(treatAsChar="1") 흐르게 삽입.
# 원본보존: 변경한 section XML + content.hpf만 다시 쓰고 BinData 이미지는 새
#   엔트리로 STORED 추가(실제 한컴 저장본도 BinData를 STORED로 보관). 나머지 보존.
# binaryItemIDRef = content.hpf <opf:item> id(예: image1) — 실제 한컴 저장본과
#   동일하게 header.xml binDataList 없이 매니페스트 id로 직접 참조한다.

_IMG_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "bmp": "image/bmp", "gif": "image/gif",
}
HWPUNIT_PER_MM = 7200.0 / 25.4  # 1mm ≈ 283.46 HWPUNIT (1인치=7200, claw H)
_PT2MM = 25.4 / 72.0
_PX_TO_HWPUNIT = 75        # 96dpi 1px = 1/96in = 75 HWPUNIT(1/7200in)
SEAL_DEFAULT_MM = 20.0     # 직인 기본 크기(세로 기준; 가로는 가로세로비 유지)


def _find_hpf_name(buf):
    """content.hpf(또는 .hpf) 매니페스트 엔트리 이름."""
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        for n in zf.namelist():
            if n.endswith("content.hpf"):
                return n
        for n in zf.namelist():
            if n.endswith(".hpf"):
                return n
    return None


def _next_bindata_name(buf, ext):
    """기존 BinData 파일명/매니페스트 id와 충돌하지 않는 (item_id, entry)."""
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = zf.namelist()
        bins = set(n for n in names if n.startswith("BinData/"))
        used_ids = set()
        hpf = next((n for n in names if n.endswith(".hpf")), None)
        if hpf:
            h = zf.read(hpf).decode("utf-8")
            used_ids = set(re.findall(r'<opf:item [^>]*\bid="([^"]+)"', h))
    n = 1
    while ("BinData/image%d.%s" % (n, ext) in bins
           or "BinData/img%d.%s" % (n, ext) in bins
           or ("image%d" % n) in used_ids):
        n += 1
    return "image%d" % n, "BinData/image%d.%s" % (n, ext)


def add_and_patch_zip(original, replacements=None, additions=None):
    """기존 엔트리(replacements)는 patch_zip_entries처럼 제자리 교체하고,
    새 엔트리(additions, STORED)는 CD 앞에 추가한다. 나머지는 바이트 보존.

    replacements/additions: {name: bytes}. additions의 이름은 기존에 없어야 한다.
    엔트리 순서/압축 방식/mimetype 첫 엔트리 규약이 그대로 유지된다.
    """
    replacements = dict(replacements or {})
    additions = dict(additions or {})
    entries, cd_offset, eocd_offset = parse_central_directory(original)
    names = {e["name"] for e in entries}
    for name in replacements:
        if name not in names:
            raise ValueError("ZIP에 없는 엔트리: %s" % name)
    for name in additions:
        if name in names:
            raise ValueError("이미 존재하는 엔트리(추가 불가): %s" % name)

    by_local = sorted(entries, key=lambda e: e["local_offset"])
    segments = []
    new_local_offset = {}
    new_meta = {}
    offset = 0

    for i, e in enumerate(by_local):
        seg_end = (by_local[i + 1]["local_offset"]
                   if i + 1 < len(by_local) else cd_offset)
        new_local_offset[e["name"]] = offset
        new_data = replacements.get(e["name"])
        if new_data is None:
            seg = original[e["local_offset"]:seg_end]
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
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            comp_data = c.compress(new_data) + c.flush()
        crc = zlib.crc32(new_data) & 0xFFFFFFFF
        flags = e["flags"] & ~0x0008
        struct.pack_into("<H", header, 6, flags)
        struct.pack_into("<I", header, 14, crc)
        struct.pack_into("<I", header, 18, len(comp_data))
        struct.pack_into("<I", header, 22, len(new_data))
        segments.append(bytes(header))
        segments.append(comp_data)
        offset += len(header) + len(comp_data)
        new_meta[e["name"]] = (flags, crc, len(comp_data), len(new_data))

    # 새 엔트리(STORED) — 로컬 헤더 + 이름 + 데이터
    added = []  # (name_bytes, local_offset, crc, size)
    dos_time, dos_date = 0, 0x21  # 1980-01-01 00:00 (유효 최소값)
    for name, data in additions.items():
        name_b = name.encode("utf-8")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        local_off = offset
        hdr = bytearray(30)
        hdr[0:4] = LOCAL_SIG
        struct.pack_into("<H", hdr, 4, 20)             # version needed
        struct.pack_into("<H", hdr, 6, 0)              # flags
        struct.pack_into("<H", hdr, 8, 0)              # method STORED
        struct.pack_into("<H", hdr, 10, dos_time)
        struct.pack_into("<H", hdr, 12, dos_date)
        struct.pack_into("<I", hdr, 14, crc)
        struct.pack_into("<I", hdr, 18, len(data))     # comp size
        struct.pack_into("<I", hdr, 22, len(data))     # uncomp size
        struct.pack_into("<H", hdr, 26, len(name_b))
        struct.pack_into("<H", hdr, 28, 0)             # extra len
        segments.append(bytes(hdr))
        segments.append(name_b)
        segments.append(data)
        offset += 30 + len(name_b) + len(data)
        added.append((name_b, local_off, crc, len(data)))

    # Central Directory — 기존 순서 유지(오프셋/메타 패치) + 새 엔트리 추가
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
    for name_b, local_off, crc, size in added:
        cd = bytearray(46)
        cd[0:4] = CD_SIG
        struct.pack_into("<H", cd, 4, 20)              # version made by
        struct.pack_into("<H", cd, 6, 20)              # version needed
        struct.pack_into("<H", cd, 8, 0)              # flags
        struct.pack_into("<H", cd, 10, 0)             # method STORED
        struct.pack_into("<H", cd, 12, dos_time)
        struct.pack_into("<H", cd, 14, dos_date)
        struct.pack_into("<I", cd, 16, crc)
        struct.pack_into("<I", cd, 20, size)
        struct.pack_into("<I", cd, 24, size)
        struct.pack_into("<H", cd, 28, len(name_b))   # name len
        struct.pack_into("<I", cd, 42, local_off)
        segments.append(bytes(cd) + name_b)
        offset += 46 + len(name_b)

    new_cd_size = offset - new_cd_offset
    total = len(entries) + len(added)
    eocd = bytearray(original[eocd_offset:])
    struct.pack_into("<H", eocd, 8, total)             # entries this disk
    struct.pack_into("<H", eocd, 10, total)            # total entries
    struct.pack_into("<I", eocd, 12, new_cd_size)
    struct.pack_into("<I", eocd, 16, new_cd_offset)
    segments.append(bytes(eocd))
    return b"".join(segments)


# ─── 이미지 픽셀 크기/가로세로비 (stdlib 헤더 파싱; claw imageAspectWH 포팅) ─

def _png_size(data):
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" \
            and data[12:16] == b"IHDR":
        w = struct.unpack_from(">I", data, 16)[0]
        h = struct.unpack_from(">I", data, 20)[0]
        return (w, h) if w and h else None
    return None


def _jpeg_size(data):
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = struct.unpack_from(">H", data, i + 5)[0]
            w = struct.unpack_from(">H", data, i + 7)[0]
            return (w, h) if w and h else None
        if i + 4 > n:
            break
        i += 2 + struct.unpack_from(">H", data, i + 2)[0]
    return None


def _bmp_size(data):
    if len(data) >= 26 and data[:2] == b"BM":
        w = abs(struct.unpack_from("<i", data, 18)[0])
        h = abs(struct.unpack_from("<i", data, 22)[0])
        return (w, h) if w and h else None
    return None


def _gif_size(data):
    if len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a"):
        w = struct.unpack_from("<H", data, 6)[0]
        h = struct.unpack_from("<H", data, 8)[0]
        return (w, h) if w and h else None
    return None


def _image_pixel_size(data):
    for fn in (_png_size, _jpeg_size, _bmp_size, _gif_size):
        r = fn(data)
        if r:
            return r
    return None


def _image_aspect(data, fallback=1.0):
    sz = _image_pixel_size(data)
    if sz and sz[1]:
        return sz[0] / sz[1]
    return fallback


# ─── 폰트 메트릭(앵커 오프셋 계산; claw estTextWidthMm/charPrFontPt 포팅) ──

def _est_text_width_mm(s, pt):
    """대략적 텍스트 폭(mm). 한글/CJK는 전각(em), ASCII는 반각(em/2)."""
    em = pt * _PT2MM
    w = 0.0
    for ch in s:
        c = ord(ch)
        wide = (0x1100 <= c <= 0x11FF or 0x3000 <= c <= 0x303F
                or 0x3130 <= c <= 0x318F or 0x4E00 <= c <= 0x9FFF
                or 0xAC00 <= c <= 0xD7A3 or 0xFF00 <= c <= 0xFFEF)
        w += em if wide else em * 0.5
    return w


def _charpr_font_pt(header_xml, char_id):
    """charPr id의 글자 크기(pt). height(1/100pt) → pt. 기본 10pt."""
    if not header_xml or char_id is None:
        return 10.0
    cid = re.escape(str(char_id))
    m = re.search(r'<hh:charPr\b[^>]*\bid="%s"[^>]*?\bheight="(\d+)"' % cid,
                  header_xml)
    if not m:
        m = re.search(r'<hh:charPr\b[^>]*\bheight="(\d+)"[^>]*?\bid="%s"' % cid,
                      header_xml)
    if m:
        return max(6.0, int(m.group(1)) / 100.0)
    return 10.0


# ─── <hp:pic> 봉투 (hwpx_helpers.make_image_para / claw buildPic와 동형) ──

def _pic_element(xml, item_id, width, height, floating=False,
                 dx_hwp=0, dy_hwp=0, rel="PARA", nat_w=None, nat_h=None):
    """단일 <hp:pic> 요소 문자열. floating이면 떠있는 직인(겹침) 배치.

    width/height: 표시 크기(HWPUNIT). nat_w/nat_h: 원본(orgSz/imgClip)용 자연 크기
    (없으면 표시 크기 사용). id/instid는 섹션 내 미사용 최소 정수로 충돌 회피.
    """
    pic_id, inst_id = _fresh_ids(xml, 2)
    nw = nat_w if nat_w else width
    nh = nat_h if nat_h else height
    cx, cy = width // 2, height // 2
    if floating:
        wrap = "IN_FRONT_OF_TEXT"
        pos = (
            'treatAsChar="0" affectLSpacing="0" flowWithText="0" '
            'allowOverlap="1" holdAnchorAndSO="0" vertRelTo="%s" horzRelTo="%s" '
            'vertAlign="TOP" horzAlign="LEFT" vertOffset="%d" horzOffset="%d"'
            % (rel, rel, dy_hwp, dx_hwp))
    else:
        wrap = "TOP_AND_BOTTOM"
        pos = (
            'treatAsChar="1" affectLSpacing="0" flowWithText="1" '
            'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
            'horzRelTo="COLUMN" vertAlign="TOP" horzAlign="CENTER" '
            'vertOffset="0" horzOffset="0"')
    return (
        '<hp:pic id="%d" zOrder="0" numberingType="PICTURE" '
        'textWrap="%s" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        'href="" groupLevel="0" instid="%d" reverse="0">'
        '<hp:offset x="0" y="0"/>'
        '<hp:orgSz width="%d" height="%d"/>'
        '<hp:curSz width="%d" height="%d"/>'
        '<hp:flip horizontal="0" vertical="0"/>'
        '<hp:rotationInfo angle="0" centerX="%d" centerY="%d" rotateimage="0"/>'
        '<hp:renderingInfo>'
        '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '</hp:renderingInfo>'
        '<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="%d" y="0"/>'
        '<hc:pt2 x="%d" y="%d"/><hc:pt3 x="0" y="%d"/></hp:imgRect>'
        '<hp:imgClip left="0" right="%d" top="0" bottom="%d"/>'
        '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        '<hp:imgDim dimwidth="%d" dimheight="%d"/>'
        '<hc:img binaryItemIDRef="%s" bright="0" contrast="0" '
        'effect="REAL_PIC" alpha="0"/><hp:effects/>'
        '<hp:sz width="%d" widthRelTo="ABSOLUTE" height="%d" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos %s/>'
        '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        '<hp:shapeComment>inserted image</hp:shapeComment>'
        '</hp:pic>'
        % (pic_id, wrap, inst_id, nw, nh, width, height, cx, cy,
           width, width, height, height, nw, nh, nw, nh, item_id,
           width, height, pos))


def _embed_image(buf, image_path):
    """이미지 바이트를 읽고 (item_id, entry, ext, data, aspect, nat_w, nat_h)."""
    ext = image_path.rsplit(".", 1)[-1].lower() if "." in image_path else ""
    if ext not in _IMG_MIME:
        raise ValueError(
            "지원하지 않는 이미지 형식: .%s (png/jpg/jpeg/bmp/gif)" % ext)
    with open(image_path, "rb") as f:
        data = f.read()
    if not data:
        raise ValueError("이미지 파일이 비어 있습니다: %s" % image_path)
    item_id, entry = _next_bindata_name(buf, ext)
    aspect = _image_aspect(data)
    px = _image_pixel_size(data)
    nat_w = px[0] * _PX_TO_HWPUNIT if px else None
    nat_h = px[1] * _PX_TO_HWPUNIT if px else None
    return item_id, entry, ext, data, aspect, nat_w, nat_h


def _register_manifest(buf, hpf_name, item_id, entry, ext):
    """content.hpf에 <opf:item> 추가한 새 XML(bytes). 이미 있으면 그대로."""
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        hpf_xml = zf.read(hpf_name).decode("utf-8")
    if ('href="%s"' % entry) in hpf_xml:
        return hpf_xml.encode("utf-8")
    item = ('<opf:item id="%s" href="%s" media-type="%s" isEmbeded="1"/>'
            % (item_id, entry, _IMG_MIME[ext]))
    if "</opf:manifest>" not in hpf_xml:
        raise ValueError("content.hpf에 <opf:manifest>가 없습니다")
    hpf_xml = hpf_xml.replace("</opf:manifest>", item + "</opf:manifest>", 1)
    return hpf_xml.encode("utf-8")


def place_seal_hwpx(src, dst, image, anchor, size_mm=SEAL_DEFAULT_MM,
                    dx_mm=0.0, dy_mm=0.0, occurrence=0, overlap=False):
    """기준 문구(anchor) 옆/위에 떠있는 직인/서명 그림을 삽입.

    문구가 든 문단(본문·표 셀 무관)의 끝에 floating <hp:pic> run을 붙인다.
    오프셋은 그 문단 기준(vertRelTo/horzRelTo="PARA").
    - 기본: 앵커 오른쪽 옆에 찍는다(글자와 안 겹침).
    - overlap=True: 도장처럼 **앵커 글자 위에 겹쳐** 찍는다(앵커 중앙 정렬,
      줄 높이에 세로 중앙). 실제 직인 날인 모양.
    dx_mm/dy_mm은 계산값에 더하는 미세조정.
    """
    if not anchor:
        raise ValueError("--anchor(기준 문구)가 필요합니다")
    if size_mm <= 0:
        raise ValueError("--size-mm은 양수여야 합니다")
    buf, names, xmls, header_xml = _load_doc(src)
    hpf_name = _find_hpf_name(buf)
    if not hpf_name:
        raise ValueError("content.hpf를 찾을 수 없습니다")
    item_id, entry, ext, data, aspect, nat_w, nat_h = _embed_image(buf, image)

    # 문구가 든 문단을 문서 순서로 수집(본문/셀 모두)
    target_section = None
    cands = []
    for n in names:
        xml = xmls[n]
        root = scan_xml(xml)
        reg = Registry(xml)
        for p_el in descendants(root, "p"):
            full = "".join(tn.text for tn in own_tnodes(p_el, reg))
            if anchor in full:
                cands.append((n, p_el, full))
        if cands:
            target_section = n
            break
    if not cands:
        raise ValueError("기준 문구를 찾을 수 없음: %r" % anchor)
    if occurrence < 0 or occurrence >= len(cands):
        raise ValueError(
            "occurrence %d 없음 (이 섹션에서 %d개 발견 — 0..%d)"
            % (occurrence, len(cands), len(cands) - 1))
    name, p_el, full = cands[occurrence]
    xml = xmls[name]

    h_hwp = max(1, int(round(size_mm * HWPUNIT_PER_MM)))
    w_hwp = max(1, int(round(size_mm * aspect * HWPUNIT_PER_MM)))
    m = re.search(r'charPrIDRef="(\d+)"', xml[p_el.start:p_el.end])
    char_id = m.group(1) if m else "0"
    pt = _charpr_font_pt(header_xml, char_id)
    idx = full.index(anchor)
    start_x = _est_text_width_mm(full[:idx], pt)
    anchor_w = _est_text_width_mm(anchor, pt)
    if overlap:
        # 앵커 글자 중앙에 직인 중앙을 맞춰 겹쳐 찍음(도장 날인)
        seal_w_mm = size_mm * aspect
        line_h_mm = pt * 0.3528                 # 글자 높이(≈pt→mm)
        dx = start_x + anchor_w / 2.0 - seal_w_mm / 2.0 + dx_mm
        dy = -(size_mm - line_h_mm) / 2.0 + dy_mm   # 줄에 세로 중앙
    else:
        dx = start_x + anchor_w + 2.0 + dx_mm   # 앵커 오른쪽 + 2mm 여백
        dy = dy_mm
    dx_hwp = int(round(dx * HWPUNIT_PER_MM))
    dy_hwp = int(round(dy * HWPUNIT_PER_MM))

    pic = _pic_element(xml, item_id, w_hwp, h_hwp, floating=True,
                       dx_hwp=dx_hwp, dy_hwp=dy_hwp, rel="PARA",
                       nat_w=nat_w, nat_h=nat_h)
    run = '<hp:run charPrIDRef="0">%s</hp:run>' % pic
    splices = [(p_el.content_end, p_el.content_end, run)]
    _strip_para_linesegs(xml, p_el, splices)
    new_xml = apply_splices(xml, splices)

    replacements = {name: new_xml.encode("utf-8"),
                    hpf_name: _register_manifest(buf, hpf_name, item_id,
                                                 entry, ext)}
    out = add_and_patch_zip(buf, replacements, {entry: data})
    with open(dst, "wb") as f:
        f.write(out)
    return {"section": name, "anchor": anchor, "occurrence": occurrence,
            "item_id": item_id, "entry": entry, "size_mm": size_mm,
            "dx_mm": round(dx, 2), "dy_mm": round(dy, 2),
            "modified_entries": sorted(replacements),
            "added_entries": [entry]}


def insert_image_hwpx(src, dst, image, after=None, para=None, inline=False,
                      width_mm=None, height_mm=None, section_idx=0):
    """일반 이미지 삽입. 기본은 기준 문단 뒤 새 문단(블록, 가운데),
    --inline이면 기준 문단 끝에 글자처럼(treatAsChar="1") 흐르게 삽입."""
    buf, names, xmls, header_xml = _load_doc(src)
    hpf_name = _find_hpf_name(buf)
    if not hpf_name:
        raise ValueError("content.hpf를 찾을 수 없습니다")
    item_id, entry, ext, data, aspect, nat_w, nat_h = _embed_image(buf, image)

    name, xml, p_el = _resolve_target_para(names, xmls, after, para,
                                           section_idx)

    # 표시 크기 결정 (HWPUNIT)
    if width_mm and height_mm:
        w_hwp = int(round(width_mm * HWPUNIT_PER_MM))
        h_hwp = int(round(height_mm * HWPUNIT_PER_MM))
    elif width_mm:
        w_hwp = int(round(width_mm * HWPUNIT_PER_MM))
        h_hwp = max(1, int(round(w_hwp / aspect)))
    elif height_mm:
        h_hwp = int(round(height_mm * HWPUNIT_PER_MM))
        w_hwp = max(1, int(round(h_hwp * aspect)))
    elif nat_w and nat_h:
        w_hwp, h_hwp = int(nat_w), int(nat_h)   # 원본 픽셀(96dpi) 크기
    else:
        w_hwp = int(round(40.0 * HWPUNIT_PER_MM))   # 폭 40mm 기본
        h_hwp = max(1, int(round(w_hwp / aspect)))
    if w_hwp <= 0 or h_hwp <= 0:
        raise ValueError("이미지 크기는 양수여야 합니다")

    if inline:
        pic = _pic_element(xml, item_id, w_hwp, h_hwp, floating=False,
                           nat_w=nat_w, nat_h=nat_h)
        m = re.search(r'charPrIDRef="(\d+)"', xml[p_el.start:p_el.end])
        char_id = m.group(1) if m else "0"
        run = '<hp:run charPrIDRef="%s">%s</hp:run>' % (char_id, pic)
        splices = [(p_el.content_end, p_el.content_end, run)]
        _strip_para_linesegs(xml, p_el, splices)
        new_xml = apply_splices(xml, splices)
        where = "inline"
    else:
        pic = _pic_element(xml, item_id, w_hwp, h_hwp, floating=False,
                           nat_w=nat_w, nat_h=nat_h)
        # _pic_element는 _fresh_ids(xml,2)로 pic/inst id를 쓰므로, 새 문단 id는
        # 세 번째 미사용 정수를 쓴다(같은 xml이라 충돌 없음).
        pid = _fresh_ids(xml, 3)[2]
        m = re.search(r'charPrIDRef="(\d+)"', xml[p_el.start:p_el.end])
        char_id = m.group(1) if m else "0"
        new_para = (
            '<hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
            'columnBreak="0" merged="0"><hp:run charPrIDRef="%s">%s</hp:run>'
            '</hp:p>' % (pid, char_id, pic))
        new_xml = apply_splices(xml, [(p_el.end, p_el.end, new_para)])
        where = "block"

    replacements = {name: new_xml.encode("utf-8"),
                    hpf_name: _register_manifest(buf, hpf_name, item_id,
                                                 entry, ext)}
    out = add_and_patch_zip(buf, replacements, {entry: data})
    with open(dst, "wb") as f:
        f.write(out)
    return {"section": name, "placement": where, "item_id": item_id,
            "entry": entry, "width_mm": round(w_hwp / HWPUNIT_PER_MM, 2),
            "height_mm": round(h_hwp / HWPUNIT_PER_MM, 2),
            "modified_entries": sorted(replacements),
            "added_entries": [entry]}


# ─── 표 구조/스타일 in-place op (claw-hwp hwpx-edit.js 포팅) ──────────
#
# 기존 표의 '모양'을 바꾼다. claw-hwp의 set_cell_background/set_cell_border/
# append_table_column/delete_table_row/merge_cells를 순수 파이썬으로 직역하되,
# 봉투(envelope) 구조/속성은 claw가 한컴 라운드트립으로 검증한 그대로 따른다.
#
# 좌표 모델: analyze/fill --cells와 동일 — --table은 해당 --section 안에서
#   문서 순서(중첩표 포함) 인덱스. row/col은 <hp:cellAddr>(rowAddr/colAddr).
# 원본보존: 변경한 section XML(+ set-cell은 header.xml)만 다시 쓴다.
# rowSpan/colSpan 병합이 있는 표는 구조 op(add-col/del-row/merge)에서 좌표
#   재계산이 불가하므로 명확히 거부(ValueError → exit 1)한다.

# header.xml <hh:borderFill> 봉투 (claw BF_ATTRS / 실제 한컴 저장본과 동일)
BF_OPEN_ATTRS = (' threeD="0" shadow="0" centerLine="NONE"'
                 ' breakCellSeparateLine="0"')
_BF_SLASH = ('<hh:slash type="NONE" Crooked="0" isCounter="0"/>'
             '<hh:backSlash type="NONE" Crooked="0" isCounter="0"/>')
_BF_BORDERS_NONE = (
    '<hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/>'
    '<hh:rightBorder type="NONE" width="0.1 mm" color="#000000"/>'
    '<hh:topBorder type="NONE" width="0.1 mm" color="#000000"/>'
    '<hh:bottomBorder type="NONE" width="0.1 mm" color="#000000"/>')
_BF_DIAGONAL = '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>'
DEFAULT_BF_INNER = _BF_SLASH + _BF_BORDERS_NONE + _BF_DIAGONAL

_HEX_RE = re.compile(r"[0-9A-Fa-f]{6}")


def _norm_hex(c):
    """RRGGBB → #RRGGBB (대문자). 6자리 16진수가 아니면 오류."""
    s = str(c).lstrip("#")
    if not re.fullmatch(_HEX_RE, s):
        raise ValueError("색은 RRGGBB 6자리 16진수여야 합니다 (예: FFE600)")
    return "#" + s.upper()


def _cell_addr(xml, tc):
    """tc 자신의 <hp:cellAddr> rowAddr/colAddr (중첩표 것 제외)."""
    ca = next((c for c in descendants(tc, "cellAddr")
               if not under_tbl_within(c, tc)), None)
    if ca is None:
        return None, None
    seg = xml[ca.start:ca.open_end]
    r = re.search(r'rowAddr="(\d+)"', seg)
    c = re.search(r'colAddr="(\d+)"', seg)
    return (int(r.group(1)) if r else None, int(c.group(1)) if c else None)


def _addr_map(xml, tbl):
    """(rowAddr, colAddr) → tc (표의 직접 셀만, 중첩표 제외)."""
    out = {}
    for tr in direct_children(tbl, "tr"):
        for tc in direct_children(tr, "tc"):
            ra, ca = _cell_addr(xml, tc)
            if ra is not None and ca is not None:
                out[(ra, ca)] = tc
    return out


def _find_cell(xml, tbl, row, col):
    """cellAddr (row,col) 셀을 찾고, 없으면 위치 인덱스로 폴백."""
    tc = _addr_map(xml, tbl).get((row, col))
    if tc is not None:
        return tc
    trs = direct_children(tbl, "tr")
    if 0 <= row < len(trs):
        tcs = direct_children(trs[row], "tc")
        if 0 <= col < len(tcs):
            return tcs[col]
    return None


def _table_has_spans(xml, tbl):
    """표의 직접 셀에 colSpan/rowSpan>1 병합이 있는지 (중첩표 제외)."""
    for tr in direct_children(tbl, "tr"):
        for tc in direct_children(tr, "tc"):
            cs = next((c for c in descendants(tc, "cellSpan")
                       if not under_tbl_within(c, tc)), None)
            if cs is None:
                continue
            seg = xml[cs.start:cs.open_end]
            for attr in ("colSpan", "rowSpan"):
                m = re.search(r'%s="(\d+)"' % attr, seg)
                if m and int(m.group(1)) != 1:
                    return True
    return False


def _get_table(xml, table_idx):
    """section XML을 스캔해 (root, tbl) 반환. 인덱스는 문서순서(중첩표 포함)."""
    root = scan_xml(xml)
    tables = list(descendants(root, "tbl"))
    if table_idx < 0 or table_idx >= len(tables):
        raise ValueError("표 인덱스 초과: %d (표 %d개)" % (table_idx, len(tables)))
    return root, tables[table_idx]


def _set_child_attr_splice(xml, owner, tag, attr, value, splices):
    """owner 자신의 <tag>(중첩표 제외) 요소의 attr 정수값을 교체하는 splice 추가."""
    el = next((c for c in descendants(owner, tag)
               if not under_tbl_within(c, owner)), None)
    if el is None:
        return
    seg = xml[el.start:el.open_end]
    m = re.search(r'\b%s="\d+"' % attr, seg)
    if m:
        splices.append((el.start + m.start(), el.start + m.end(),
                        '%s="%s"' % (attr, value)))


def _bump_count(xml, tbl, attr, delta, splices):
    """tbl 여는 태그의 rowCnt/colCnt 등을 delta만큼 보정하는 splice 추가."""
    seg = xml[tbl.start:tbl.open_end]
    m = re.search(r'\b%s="(\d+)"' % attr, seg)
    if m:
        new = max(0, int(m.group(1)) + delta)
        splices.append((tbl.start + m.start(), tbl.start + m.end(),
                        '%s="%s"' % (attr, new)))


# ── header.xml borderFill 찾기/추가 ──────────────────────────────────

def _bf_by_id(header_xml, bid):
    """header.xml에서 id=bid인 <hh:borderFill>의 (inner, open_tag) 반환."""
    root = scan_xml(header_xml)
    for bf in descendants(root, "borderFill"):
        m = re.search(r'\bid="(\d+)"', header_xml[bf.start:bf.open_end])
        if m and m.group(1) == str(bid):
            return (header_xml[bf.content_start:bf.content_end],
                    header_xml[bf.start:bf.open_end])
    return None, None


def _ensure_borderfill(header_xml, want_inner, template_open=None):
    """want_inner와 동일한 borderFill이 있으면 그 id를, 없으면 새로 추가하고
    id를 반환. 새로 추가 시 <hh:borderFills itemCnt>를 +1 보정.

    Returns: (id_str, new_header_xml). 재사용이면 new_header_xml == header_xml.
    """
    root = scan_xml(header_xml)
    lists = list(descendants(root, "borderFills"))
    if not lists:
        raise ValueError("header.xml에 <hh:borderFills>가 없습니다")
    bflist = lists[0]
    ids = []
    for bf in descendants(bflist, "borderFill"):
        if under_tbl_within(bf, bflist):
            continue
        m = re.search(r'\bid="(\d+)"', header_xml[bf.start:bf.open_end])
        if header_xml[bf.content_start:bf.content_end] == want_inner:
            return m.group(1), header_xml  # 동일 borderFill 재사용
        if m:
            ids.append(int(m.group(1)))
    new_id = (max(ids) + 1) if ids else 1
    if template_open:
        new_open = re.sub(r'\bid="\d+"', 'id="%d"' % new_id, template_open, count=1)
    else:
        new_open = '<hh:borderFill id="%d"%s>' % (new_id, BF_OPEN_ATTRS)
    new_bf = new_open + want_inner + "</hh:borderFill>"
    splices = [(bflist.content_end, bflist.content_end, new_bf)]
    open_seg = header_xml[bflist.start:bflist.open_end]
    m = re.search(r'itemCnt="(\d+)"', open_seg)
    if m:
        splices.append((bflist.start + m.start(), bflist.start + m.end(),
                        'itemCnt="%d"' % (int(m.group(1)) + 1)))
    return str(new_id), apply_splices(header_xml, splices)


def _set_fill_inner(inner, hexc):
    """borderFill inner에 <hc:fillBrush> 배경색을 설정(있으면 교체, 없으면 추가)."""
    brush = ('<hc:fillBrush><hc:winBrush faceColor="%s" hatchColor="#999999"'
             ' alpha="0"/></hc:fillBrush>' % hexc)
    if "<hc:fillBrush" in inner:
        return re.sub(r'<hc:fillBrush\b[^>]*/>|<hc:fillBrush\b.*?</hc:fillBrush>',
                      brush, inner, count=1, flags=re.S)
    return inner + brush


def _set_border_inner(inner, on):
    """4면 테두리를 SOLID(on) 또는 NONE(off)로 설정."""
    for side in ("left", "right", "top", "bottom"):
        if on:
            new = ('<hh:%sBorder type="SOLID" width="0.4 mm" color="#000000"/>'
                   % side)
        else:
            new = ('<hh:%sBorder type="NONE" width="0.1 mm" color="#000000"/>'
                   % side)
        pat = r'<hh:%sBorder\b[^>]*?/>' % side
        if re.search(pat, inner):
            inner = re.sub(pat, new, inner, count=1)
        else:
            inner = _BF_SLASH_split(inner, new)
    return inner


def _BF_SLASH_split(inner, border_tag):
    """테두리 태그가 없을 때 backSlash 뒤(슬래시 다음)에 삽입 — 스키마 순서 유지."""
    idx = inner.find("</hh:backSlash>")
    if idx >= 0:
        idx += len("</hh:backSlash>")
        return inner[:idx] + border_tag + inner[idx:]
    pos = inner.rfind("<hh:diagonal")
    if pos >= 0:
        return inner[:pos] + border_tag + inner[pos:]
    return inner + border_tag


def _header_name(buf):
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        return next((n for n in zf.namelist() if _HEADER_XML_RE.search(n)), None)


def set_cell_style_hwpx(src, dst, table_idx, row, col,
                        bg=None, border=None, section_idx=0):
    """셀 배경색(--bg) 및/또는 테두리(--border on/off) 설정.

    셀의 현재 borderFillIDRef가 가리키는 borderFill을 복제해 요청한 변경을
    적용한 새 borderFill을 header.xml에 (없으면) 추가하고, 셀의 borderFillIDRef
    를 그쪽으로 바꾼다. 같은 셀만 영향을 받으므로 다른 셀의 모양은 보존된다.

    ★ claw 대비 의도적 분기: claw-hwp는 set_cell_background/border에 <hp:cellzone>
    + char-shade를 쓰지만, 여기서는 셀 자신의 borderFillIDRef를 복제·변형한
    borderFill로 repoint한다. 이는 실제 한컴 저장본이 셀 배경/테두리를 표현하는
    네이티브 방식(borderFill의 fillBrush faceColor + per-side border)과 동일하다.
    claw 재동기화 시 이 부분을 cellzone으로 '되돌리지' 말 것.
    """
    if bg is None and border is None:
        raise ValueError("--bg 또는 --border 중 하나는 지정해야 합니다")
    buf, names, xmls, header_xml = _load_doc(src)
    if header_xml is None:
        raise ValueError("header.xml을 찾을 수 없습니다")
    if section_idx >= len(names):
        raise ValueError("섹션 인덱스 초과: %d" % section_idx)
    name = names[section_idx]
    xml = xmls[name]
    _, tbl = _get_table(xml, table_idx)
    tc = _find_cell(xml, tbl, row, col)
    if tc is None:
        raise ValueError("셀을 찾을 수 없음: row=%d col=%d" % (row, col))
    open_seg = xml[tc.start:tc.open_end]
    m = re.search(r'borderFillIDRef="(\d+)"', open_seg)
    cur_ref = m.group(1) if m else None
    base_inner, base_open = (_bf_by_id(header_xml, cur_ref)
                             if cur_ref else (None, None))
    if base_inner is None:
        base_inner, base_open = DEFAULT_BF_INNER, None
    new_inner = base_inner
    if bg is not None:
        new_inner = _set_fill_inner(new_inner, _norm_hex(bg))
    if border is not None:
        new_inner = _set_border_inner(new_inner, bool(border))
    new_id, new_header = _ensure_borderfill(header_xml, new_inner, base_open)

    sp = []
    if m:
        sp.append((tc.start + m.start(), tc.start + m.end(),
                   'borderFillIDRef="%s"' % new_id))
    else:
        ins = tc.open_end - 1  # 여는 태그 '>' 직전
        sp.append((ins, ins, ' borderFillIDRef="%s"' % new_id))
    changed = {name: apply_splices(xml, sp)}
    if new_header != header_xml:
        hname = _header_name(buf)
        if hname is None:
            raise ValueError("header.xml 엔트리를 찾을 수 없습니다")
        changed[hname] = new_header
    _write_doc(buf, dst, changed)
    return {"table": table_idx, "row": row, "col": col, "section": section_idx,
            "bg": _norm_hex(bg) if bg is not None else None,
            "border": (None if border is None else bool(border)),
            "borderFillIDRef": new_id, "modified_entries": sorted(changed)}


# ── add-col / del-row / merge-cells ──────────────────────────────────

def _clone_cell(frag, value, col_addr, next_pid):
    """셀 조각 XML 복제 — 텍스트 교체(기본 빈칸) + colAddr 갱신 + 문단 id 고유화
    + linesegarray 제거(복제 캐시는 항상 stale)."""
    root = scan_xml(frag)
    tc = root.children[0]
    reg = Registry(frag)
    replace_cell_text(tc, "" if value is None else str(value), reg)
    splices = build_splices(frag, reg)
    _strip_all_linesegarray(tc, splices)
    for ca in descendants(tc, "cellAddr"):
        if under_tbl_within(ca, tc):
            continue
        m = re.search(r'\bcolAddr="\d+"', frag[ca.start:ca.open_end])
        if m:
            splices.append((ca.start + m.start(), ca.start + m.end(),
                            'colAddr="%d"' % col_addr))
    for p_el in descendants(tc, "p"):
        m = re.search(r'\bid="\d+"', frag[p_el.start:p_el.open_end])
        if m:
            splices.append((p_el.start + m.start(), p_el.start + m.end(),
                            'id="%d"' % next_pid[0]))
            next_pid[0] += 1
    return apply_splices(frag, splices)


def add_table_column(xml, table_idx, cells_values=None, at=None):
    """표 끝(기본) 또는 --at 위치에 열 추가. 각 tr에 셀 1개씩 삽입,
    colCnt +1, 새 셀 colAddr 정합(뒤 셀 colAddr +1), cellSz는 복제로 보존."""
    _, tbl = _get_table(xml, table_idx)
    if _table_has_spans(xml, tbl):
        raise ValueError("colSpan/rowSpan 병합이 있는 표는 열 추가 미지원 — "
                         "셀 좌표가 깨질 수 있어 거부합니다")
    trs = direct_children(tbl, "tr")
    if not trs:
        raise ValueError("표에 행이 없습니다")
    ncols = max(len(direct_children(tr, "tc")) for tr in trs)
    insert_index = ncols if at is None else max(0, min(int(at), ncols))
    max_id = 0
    for m in re.finditer(r'\bid="(\d+)"', xml):
        max_id = max(max_id, int(m.group(1)))
    next_pid = [max_id + 1]

    splices = []
    for ri, tr in enumerate(trs):
        tcs = direct_children(tr, "tc")
        if not tcs:
            continue
        val = (cells_values[ri] if cells_values and ri < len(cells_values)
               else None)
        tmpl_idx = insert_index if insert_index < len(tcs) else len(tcs) - 1
        template = tcs[tmpl_idx]
        clone = _clone_cell(xml[template.start:template.end], val,
                            insert_index, next_pid)
        if insert_index < len(tcs):
            pos = tcs[insert_index].start
        else:
            pos = tcs[-1].end
        splices.append((pos, pos, clone))
        for j in range(insert_index, len(tcs)):
            _set_child_attr_splice(xml, tcs[j], "cellAddr", "colAddr",
                                   j + 1, splices)
    _bump_count(xml, tbl, "colCnt", +1, splices)
    return apply_splices(xml, splices)


def delete_table_row(xml, table_idx, row):
    """행 삭제 — tr 제거, rowCnt -1, 뒤 행들의 cellAddr rowAddr -1."""
    _, tbl = _get_table(xml, table_idx)
    if _table_has_spans(xml, tbl):
        raise ValueError("colSpan/rowSpan 병합이 있는 표는 행 삭제 미지원 — "
                         "셀 좌표가 깨질 수 있어 거부합니다")
    trs = direct_children(tbl, "tr")
    if row < 0 or row >= len(trs):
        raise ValueError("행 인덱스 초과: %d (행 %d개)" % (row, len(trs)))
    splices = [(trs[row].start, trs[row].end, "")]
    for i in range(row + 1, len(trs)):
        for tc in direct_children(trs[i], "tc"):
            _set_child_attr_splice(xml, tc, "cellAddr", "rowAddr",
                                   i - 1, splices)
    _bump_count(xml, tbl, "rowCnt", -1, splices)
    return apply_splices(xml, splices)


def merge_table_cells(xml, table_idx, row, col, row2, col2):
    """사각범위 (row,col)~(row2,col2) 병합 — 앵커(좌상단) 셀에 colSpan/rowSpan
    설정, 덮인 셀 제거. cellAddr 그리드는 그대로 두므로 rowCnt/colCnt 불변."""
    r1, r2 = sorted((int(row), int(row2)))
    c1, c2 = sorted((int(col), int(col2)))
    if r1 == r2 and c1 == c2:
        raise ValueError("병합 범위가 셀 1개입니다 (2개 이상 지정)")
    _, tbl = _get_table(xml, table_idx)
    if _table_has_spans(xml, tbl):
        raise ValueError("이미 병합(colSpan/rowSpan)이 있는 표는 재병합 미지원 — "
                         "셀 좌표가 깨질 수 있어 거부합니다")
    amap = _addr_map(xml, tbl)
    anchor = amap.get((r1, c1))
    if anchor is None:
        raise ValueError("앵커 셀 없음: row=%d col=%d" % (r1, c1))
    missing = [(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)
               if (r, c) not in amap]
    if missing:
        raise ValueError("병합 범위에 없는 셀 좌표: %s" % missing)
    splices = []
    removed = 0
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            if r == r1 and c == c1:
                continue
            tc = amap[(r, c)]
            splices.append((tc.start, tc.end, ""))
            removed += 1
    _set_child_attr_splice(xml, anchor, "cellSpan", "colSpan", c2 - c1 + 1,
                           splices)
    _set_child_attr_splice(xml, anchor, "cellSpan", "rowSpan", r2 - r1 + 1,
                           splices)
    for lsa in descendants(anchor, "linesegarray"):
        if not under_tbl_within(lsa, anchor):
            splices.append((lsa.start, lsa.end, ""))
    return apply_splices(xml, splices), removed


def _table_op_doc(src, dst, section_idx, fn):
    """section을 열어 fn(xml)→new_xml(또는 (new_xml, extra))을 적용해 저장."""
    buf, names, xmls, _ = _load_doc(src)
    if section_idx >= len(names):
        raise ValueError("섹션 인덱스 초과: %d" % section_idx)
    name = names[section_idx]
    result = fn(xmls[name])
    extra = None
    if isinstance(result, tuple):
        new_xml, extra = result
    else:
        new_xml = result
    _write_doc(buf, dst, {name: new_xml})
    return name, extra


# ─── 글자/문단 서식 in-place (claw-hwp apply_text_style/paragraph_style 포팅) ─
#
# 기존 .hwpx 본문 문단의 글자모양(charPr)·문단모양(paraPr)을 바꾼다. 대상 문단의
# 현재 charPr/paraPr를 복제해 요청한 변경을 적용한 새 모양을 header.xml에 추가
# (itemCnt 보정)하고, 문단의 run charPrIDRef / 문단 paraPrIDRef를 그쪽으로 바꾼다.
# 복제 기반이라 대상 문단만 영향을 받고 나머지는 보존된다. 수정 문단의 stale
# linesegarray는 제거(한컴이 재계산). bold/italic은 <hh:underline> 앞에 둔다(한컴 순서).

_ALIGN_MAP = {"left": "LEFT", "center": "CENTER", "right": "RIGHT",
              "justify": "JUSTIFY", "both": "BOTH"}


def _set_open_attr(open_tag, attr, value):
    """여는 태그(끝이 '>')의 attr를 교체 또는 추가."""
    if re.search(r'\b%s="[^"]*"' % attr, open_tag):
        return re.sub(r'\b%s="[^"]*"' % attr,
                      '%s="%s"' % (attr, value), open_tag, count=1)
    return open_tag[:-1] + ' %s="%s">' % (attr, value)


def _elem_by_id(header_xml, tag, eid):
    """header.xml에서 id=eid인 <hh:tag>의 (open_tag, inner) 반환."""
    root = scan_xml(header_xml)
    for el in descendants(root, tag):
        m = re.search(r'\bid="(\d+)"', header_xml[el.start:el.open_end])
        if m and m.group(1) == str(eid):
            return (header_xml[el.start:el.open_end],
                    header_xml[el.content_start:el.content_end])
    return None, None


def _add_cloned(header_xml, list_tag, item_tag, base_id, mutate):
    """base_id 모양을 복제·변형해 list_tag에 추가하고 itemCnt +1. (new_id, header)."""
    open_tag, inner = _elem_by_id(header_xml, item_tag, base_id)
    if open_tag is None:
        open_tag, inner = _elem_by_id(header_xml, item_tag, "0")
    if open_tag is None:
        raise ValueError("header.xml에 <hh:%s>가 없습니다" % item_tag)
    root = scan_xml(header_xml)
    lst = next(iter(descendants(root, list_tag)), None)
    if lst is None:
        raise ValueError("header.xml에 <hh:%s>가 없습니다" % list_tag)
    ids = []
    for el in descendants(lst, item_tag):
        m = re.search(r'\bid="(\d+)"', header_xml[el.start:el.open_end])
        if m:
            ids.append(int(m.group(1)))
    new_id = (max(ids) + 1) if ids else 0
    new_open = re.sub(r'\bid="\d+"', 'id="%d"' % new_id, open_tag, count=1)
    new_open, new_inner = mutate(new_open, inner)
    new_el = new_open + new_inner + "</hh:%s>" % item_tag
    splices = [(lst.content_end, lst.content_end, new_el)]
    seg = header_xml[lst.start:lst.open_end]
    m = re.search(r'itemCnt="(\d+)"', seg)
    if m:
        splices.append((lst.start + m.start(), lst.start + m.end(),
                        'itemCnt="%d"' % (int(m.group(1)) + 1)))
    return str(new_id), apply_splices(header_xml, splices)


def _mutate_charpr(bold, italic, underline, color, size_pt):
    def m(open_tag, inner):
        if size_pt is not None:
            open_tag = _set_open_attr(open_tag, "height",
                                      str(int(round(float(size_pt) * 100))))
        if color:
            open_tag = _set_open_attr(open_tag, "textColor",
                                      "#" + str(color).lstrip("#").upper())

        def ins_before_underline(s, tag):
            if re.search(r'<%s\b' % tag.replace(":", r"\:"), s):
                return s
            um = re.search(r'<hh:underline\b', s)
            snip = "<%s/>" % tag
            return (s[:um.start()] + snip + s[um.start():]) if um else s + snip
        if bold:
            inner = ins_before_underline(inner, "hh:bold")
        if italic:
            inner = ins_before_underline(inner, "hh:italic")
        if underline:
            ul = '<hh:underline type="BOTTOM" shape="SOLID" color="#000000"/>'
            inner = (re.sub(r'<hh:underline\b[^>]*/>', ul, inner, count=1)
                     if re.search(r'<hh:underline\b[^>]*/>', inner)
                     else inner + ul)
        return open_tag, inner
    return m


def _mutate_parapr(align, line_spacing):
    def m(open_tag, inner):
        if align:
            al = _ALIGN_MAP[align.lower()]
            if re.search(r'<hh:align\b[^>]*\bhorizontal="[^"]*"', inner):
                inner = re.sub(r'(<hh:align\b[^>]*\bhorizontal=")[^"]*(")',
                               lambda mm: mm.group(1) + al + mm.group(2),
                               inner, count=1)
        if line_spacing is not None:
            if re.search(r'<hh:lineSpacing\b[^>]*\bvalue="[^"]*"', inner):
                inner = re.sub(r'(<hh:lineSpacing\b[^>]*\bvalue=")[^"]*(")',
                               lambda mm: mm.group(1) + str(line_spacing)
                               + mm.group(2), inner, count=1)
                inner = re.sub(r'(<hh:lineSpacing\b[^>]*\btype=")[^"]*(")',
                               lambda mm: mm.group(1) + "PERCENT" + mm.group(2),
                               inner, count=1)
        return open_tag, inner
    return m


def _resolve_target_para(names, xmls, after, para, section_idx):
    """(section_name, section_xml, para_el) — --after 문구 또는 --para 인덱스."""
    if after is not None:
        for n in names:
            xml = xmls[n]
            root = scan_xml(xml)
            reg = Registry(xml)
            for p in descendants(root, "p"):
                if not _para_not_in_table(p):
                    continue
                full = "".join(tn.text for tn in own_tnodes(p, reg))
                if after in full:
                    return n, xml, p
        raise ValueError("기준 문구를 찾을 수 없음: %r" % after)
    if section_idx >= len(names):
        raise ValueError("섹션 인덱스 초과: %d" % section_idx)
    n = names[section_idx]
    xml = xmls[n]
    _, paras = _body_paragraphs(scan_xml(xml))
    if not paras:
        raise ValueError("첫 섹션에 본문 문단(<hp:p>)이 없습니다")
    idx = -1 if para is None else para
    try:
        return n, xml, paras[idx]
    except IndexError:
        raise ValueError("문단 인덱스 초과: %d (문단 %d개)" % (idx, len(paras)))


def _strip_para_linesegs(section_xml, para, splices):
    for lsa in descendants(para, "linesegarray"):
        if not ancestor_within(lsa, ("p",), para):
            splices.append((lsa.start, lsa.end, ""))


def set_text_style_hwpx(src, dst, after=None, para=None, bold=False,
                        italic=False, underline=False, color=None,
                        size_pt=None, section_idx=0):
    """대상 문단의 모든 run에 글자모양(굵게/기울임/밑줄/색/크기) 적용."""
    if not (bold or italic or underline or color or size_pt is not None):
        raise ValueError("스타일(--bold/--italic/--underline/--color/--size) 중 "
                         "하나는 지정해야 합니다")
    buf, names, xmls, header_xml = _load_doc(src)
    if header_xml is None:
        raise ValueError("header.xml이 없습니다")
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        header_name = next((n for n in zf.namelist()
                            if _HEADER_XML_RE.search(n)), None)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    runs = [r for r in descendants(para_el, ("run", "r"))
            if not under_tbl_within(r, para_el)]
    base_cid = "0"
    for r in runs:
        m = re.search(r'charPrIDRef="(\d+)"', sec_xml[r.start:r.open_end])
        if m:
            base_cid = m.group(1)
            break
    new_id, new_header = _add_cloned(
        header_xml, "charProperties", "charPr", base_cid,
        _mutate_charpr(bold, italic, underline, color, size_pt))
    splices = []
    for r in runs:
        seg = sec_xml[r.start:r.open_end]
        m = re.search(r'charPrIDRef="\d+"', seg)
        if m:
            splices.append((r.start + m.start(), r.start + m.end(),
                            'charPrIDRef="%s"' % new_id))
    _strip_para_linesegs(sec_xml, para_el, splices)
    new_sec = apply_splices(sec_xml, splices)
    _write_doc(buf, dst, {header_name: new_header, sec_name: new_sec})
    return {"action": "text-style", "section": sec_name, "charPrId": new_id,
            "runs": len(runs),
            "applied": {"bold": bold, "italic": italic, "underline": underline,
                        "color": color, "size_pt": size_pt}}


def set_para_style_hwpx(src, dst, after=None, para=None, align=None,
                        line_spacing=None, section_idx=0):
    """대상 문단의 문단모양(정렬/줄간격) 적용."""
    if not align and line_spacing is None:
        raise ValueError("--align 또는 --line-spacing 중 하나는 지정해야 합니다")
    if align and align.lower() not in _ALIGN_MAP:
        raise ValueError("--align은 %s 중 하나" % "/".join(_ALIGN_MAP))
    buf, names, xmls, header_xml = _load_doc(src)
    if header_xml is None:
        raise ValueError("header.xml이 없습니다")
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        header_name = next((n for n in zf.namelist()
                            if _HEADER_XML_RE.search(n)), None)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    m = re.search(r'paraPrIDRef="(\d+)"', sec_xml[para_el.start:para_el.open_end])
    base_pid = m.group(1) if m else "0"
    new_id, new_header = _add_cloned(
        header_xml, "paraProperties", "paraPr", base_pid,
        _mutate_parapr(align, line_spacing))
    splices = []
    m = re.search(r'paraPrIDRef="\d+"', sec_xml[para_el.start:para_el.open_end])
    if m:
        splices.append((para_el.start + m.start(), para_el.start + m.end(),
                        'paraPrIDRef="%s"' % new_id))
    _strip_para_linesegs(sec_xml, para_el, splices)
    new_sec = apply_splices(sec_xml, splices)
    _write_doc(buf, dst, {header_name: new_header, sec_name: new_sec})
    return {"action": "para-style", "section": sec_name, "paraPrId": new_id,
            "applied": {"align": align, "line_spacing": line_spacing}}


# ─── 각주·미주·하이퍼링크·책갈피 in-place (claw-hwp hwpx-edit.js 포팅) ─
#
# 모두 대상 본문 문단(top-level, 표 밖; _resolve_target_para)에 컨트롤 run을
# 주입한다. 봉투(envelope) 구조/속성은 claw가 한컴 라운드트립으로 다듬은 그대로
# 따른다. 각주/미주의 note id·subList id 등은 claw의 안전 기본값(id="0")을 쓴다.
# 수정 문단의 stale linesegarray(줄배치 캐시)는 항상 제거 — 한컴 '손상 문서'
# 경고 방지(build_splices/P8과 동일 정책). header.xml·기타 엔트리는 건드리지
# 않고(하이퍼링크만 charPr 1개 추가) 변경 섹션만 다시 쓴다(원본 바이트 보존).


def _para_first_charpr(sec_xml, para_el):
    """대상 문단 안에서 처음 만나는 charPrIDRef (없으면 "0")."""
    m = re.search(r'charPrIDRef="(\d+)"',
                  sec_xml[para_el.content_start:para_el.content_end])
    return m.group(1) if m else "0"


def _append_run_to_para(sec_xml, para_el, run_xml):
    """문단 끝(컨텐츠 끝)에 run을 삽입하고 그 문단의 stale linesegarray 제거."""
    splices = [(para_el.content_end, para_el.content_end, run_xml)]
    _strip_para_linesegs(sec_xml, para_el, splices)
    return apply_splices(sec_xml, splices)


def _note_run_xml(kind, char_id, text):
    """각주/미주 컨트롤 run (claw opInsertNote 봉투 1:1 포팅).

    kind는 "footNote" 또는 "endNote". 참조 표식(¹ ²)·페이지 하단 배치는 한컴이
    컨트롤 위치로부터 렌더하므로 우리는 컨트롤만 문단 끝에 둔다.
    """
    tag = "hp:%s" % kind
    return (
        '<hp:run charPrIDRef="%s"><hp:ctrl>'
        '<%s id="0">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" '
        'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="0"><hp:t>%s</hp:t></hp:run>'
        '</hp:p></hp:subList></%s></hp:ctrl></hp:run>'
        % (char_id, tag, escape_text(text), tag))


def add_note_hwpx(src, dst, kind, text, after=None, para=None, section_idx=0):
    """각주(footNote)/미주(endNote) 컨트롤을 대상 문단 끝에 삽입."""
    if not text or not str(text).strip():
        raise ValueError("--text(주석 내용)가 필요합니다")
    if kind not in ("footNote", "endNote"):
        raise ValueError("kind는 footNote/endNote 중 하나여야 합니다")
    buf, names, xmls, _ = _load_doc(src)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    char_id = _para_first_charpr(sec_xml, para_el)
    run = _note_run_xml(kind, char_id, str(text))
    new_sec = _append_run_to_para(sec_xml, para_el, run)
    _write_doc(buf, dst, {sec_name: new_sec})
    return {"action": "footnote" if kind == "footNote" else "endnote",
            "section": sec_name, "text": text}


def _hyperlink_run_xml(char_id, begin_id, field_id, url, text):
    """클릭 가능한 HYPERLINK 필드 run (claw opInsertHyperlink 봉투 1:1 포팅)."""
    u = escape_text(url)
    return (
        '<hp:run charPrIDRef="%s"><hp:ctrl>'
        '<hp:fieldBegin id="%d" type="HYPERLINK" name="" editable="0" '
        'dirty="1" zorder="-1" fieldid="%d">'
        '<hp:parameters cnt="6" name="">'
        '<hp:integerParam name="Prop">0</hp:integerParam>'
        '<hp:stringParam name="Command">%s;1;0;0;</hp:stringParam>'
        '<hp:stringParam name="Path">%s</hp:stringParam>'
        '<hp:stringParam name="Category">HWPHYPERLINK_TYPE_URL</hp:stringParam>'
        '<hp:stringParam name="TargetType">'
        'HWPHYPERLINK_TARGET_BOOKMARK</hp:stringParam>'
        '<hp:stringParam name="DocOpenType">'
        'HWPHYPERLINK_JUMP_CURRENTTAB</hp:stringParam>'
        '</hp:parameters></hp:fieldBegin></hp:ctrl>'
        '<hp:t>%s</hp:t>'
        '<hp:ctrl><hp:fieldEnd beginIDRef="%d" fieldid="%d"/></hp:ctrl>'
        '</hp:run>'
        % (char_id, begin_id, field_id, u, u, escape_text(text),
           begin_id, field_id))


def add_hyperlink_hwpx(src, dst, url, text, after=None, para=None,
                       section_idx=0):
    """대상 문단 끝에 클릭 가능한 URL 하이퍼링크 필드를 삽입.

    웹 링크 룩을 위해 대상 문단의 charPr을 파란색+밑줄로 복제해 링크 run에
    물린다(P8 set-text-style과 동일한 _add_cloned 트릭). 필드 자체는 charPr과
    무관하게 동작하므로 스타일은 부가 효과.
    """
    if not url or not str(url).strip():
        raise ValueError("--url(링크 주소)이 필요합니다")
    if not text or not str(text).strip():
        raise ValueError("--text(표시 문구)가 필요합니다")
    buf, names, xmls, header_xml = _load_doc(src)
    if header_xml is None:
        raise ValueError("header.xml이 없습니다")
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        header_name = next((n for n in zf.namelist()
                            if _HEADER_XML_RE.search(n)), None)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    base_cid = _para_first_charpr(sec_xml, para_el)
    new_cid, new_header = _add_cloned(
        header_xml, "charProperties", "charPr", base_cid,
        _mutate_charpr(False, False, True, "0000FF", None))
    ids = _fresh_ids(sec_xml, 2)
    run = _hyperlink_run_xml(new_cid, ids[0], ids[1], str(url), str(text))
    new_sec = _append_run_to_para(sec_xml, para_el, run)
    _write_doc(buf, dst, {header_name: new_header, sec_name: new_sec})
    return {"action": "hyperlink", "section": sec_name, "charPrId": new_cid,
            "url": url, "text": text}


def add_bookmark_hwpx(src, dst, name, after=None, para=None, section_idx=0):
    """대상 문단 첫 run 시작에 책갈피(bookmark) 마커를 삽입.

    책갈피는 상호참조/'찾아가기'의 점프 대상이 되는 이름 앵커다(claw
    opInsertBookmark 포팅). 첫 run이 없거나 self-closing이면 새 run을 앞에 둔다.
    """
    if not name or not str(name).strip():
        raise ValueError("--name(책갈피 이름)이 필요합니다")
    buf, names, xmls, _ = _load_doc(src)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    ctrl = ('<hp:ctrl><hp:bookmark name="%s"/></hp:ctrl>'
            % escape_text(str(name)))
    runs = [c for c in para_el.children
            if c.name in ("run", "r") and not c.self_closing]
    splices = []
    if runs:
        pos = runs[0].content_start
        splices.append((pos, pos, ctrl))
    else:
        char_id = _para_first_charpr(sec_xml, para_el)
        run = '<hp:run charPrIDRef="%s">%s</hp:run>' % (char_id, ctrl)
        splices.append((para_el.content_start, para_el.content_start, run))
    _strip_para_linesegs(sec_xml, para_el, splices)
    new_sec = apply_splices(sec_xml, splices)
    _write_doc(buf, dst, {sec_name: new_sec})
    return {"action": "bookmark", "section": sec_name, "name": name}


# ─── P9: 페이지 설정·다단·쪽/단 나누기 ──────────────────────────────
#
# claw-hwp hwpx-edit.js의 set_page_break/set_column_break/set_columns/
# set_page_setup를 Python stdlib로 직역. 핵심 철칙: secPr(pagePr/margin)은
# '한컴 손상 문서' 최대 민감부 → 기존 자식/속성을 보존하며 속성값만 정확히
# 변경(구조·태그 추가/삭제 금지). check_openable이 secPr 완전성을 본다.

# 편집 용지 프리셋 (mm) — 세로(portrait) 기준 (너비, 높이)
_PAGE_SIZES_MM = {
    "a3": (297.0, 420.0), "a4": (210.0, 297.0), "a5": (148.0, 210.0),
    "b4": (257.0, 364.0), "b5": (176.0, 250.0), "b6": (128.0, 182.0),
    "letter": (215.9, 279.4), "legal": (215.9, 355.6),
}


def _mm_to_hu(mm):
    return int(round(float(mm) * HWPUNIT_PER_MM))


def set_para_break_hwpx(src, dst, kind, after=None, para=None, on=True,
                        section_idx=0):
    """대상 본문 문단에 쪽/단 나누기(pageBreak/columnBreak) 속성 설정.

    kind: "page" → pageBreak, "column" → columnBreak. on=False면 0으로 해제.
    표 안 문단이 아닌 본문 <hp:p>만 대상(_resolve_target_para).
    """
    attr = {"page": "pageBreak", "column": "columnBreak"}[kind]
    buf, names, xmls, _ = _load_doc(src)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    open_tag = sec_xml[para_el.start:para_el.open_end]
    new_open = _set_open_attr(open_tag, attr, "1" if on else "0")
    changed = {}
    if new_open != open_tag:
        new_sec = sec_xml[:para_el.start] + new_open + sec_xml[para_el.open_end:]
        changed[sec_name] = new_sec
    _write_doc(buf, dst, changed)
    return {"action": "%s-break" % kind, "section": sec_name, "attr": attr,
            "on": bool(on)}


def _apply_colpr(xml, n, gap):
    """섹션 XML의 모든 <hp:colPr/>에 다단(colCount/sameSz/sameGap) 적용."""
    cnt = [0]

    def repl(m):
        tag = m.group(0)
        if re.search(r'\bcolCount="\d+"', tag):
            tag = re.sub(r'\bcolCount="\d+"', 'colCount="%d"' % n, tag)
        else:
            tag = re.sub(r'\s*/>$', ' colCount="%d"/>' % n, tag)
        if re.search(r'\bsameSz="\d+"', tag):
            tag = re.sub(r'\bsameSz="\d+"', 'sameSz="1"', tag)
        else:
            tag = re.sub(r'\s*/>$', ' sameSz="1"/>', tag)
        if re.search(r'\bsameGap="\d+"', tag):
            tag = re.sub(r'\bsameGap="\d+"', 'sameGap="%d"' % gap, tag)
        else:
            tag = re.sub(r'\s*/>$', ' sameGap="%d"/>' % gap, tag)
        cnt[0] += 1
        return tag

    return re.sub(r'<hp:colPr\b[^>]*/>', repl, xml), cnt[0]


def set_columns_hwpx(src, dst, count, gap_mm=None):
    """모든 섹션의 secPr 다단(단/칼럼) 설정. count=1이면 단일 단으로 복귀."""
    n = int(count)
    if n < 1:
        raise ValueError("--count는 1 이상의 정수여야 합니다")
    if n > 1:
        gap = _mm_to_hu(gap_mm) if gap_mm is not None else 1134  # 기본≈4mm
    else:
        gap = 0
    buf, names, xmls, _ = _load_doc(src)
    changed, total = {}, 0
    for name in names:
        new_xml, c = _apply_colpr(xmls[name], n, gap)
        if c and new_xml != xmls[name]:
            changed[name] = new_xml
        total += c
    if total == 0:
        raise ValueError("set-columns: <hp:colPr>를 찾지 못했습니다 (정상 HWPX 아님)")
    _write_doc(buf, dst, changed)
    return {"action": "set-columns", "count": n, "gap_hwpunit": gap,
            "colpr_total": total, "sections_changed": list(changed)}


def _margin_repl(tag, g):
    """<hp:margin/>의 left/right/top/bottom만 g로 변경(header/footer/gutter 보존)."""
    for a in ("left", "right", "top", "bottom"):
        if re.search(r'\b%s="\d+"' % a, tag):
            tag = re.sub(r'\b%s="\d+"' % a, '%s="%d"' % (a, g), tag)
    return tag


def _apply_pagepr(xml, orientation, margin_mm, size, width_mm, height_mm):
    """섹션 XML의 모든 <hp:pagePr ...>(용지 크기/방향) + <hp:margin/>(여백) 변경."""
    cnt = [0]

    def repl(m):
        tag = m.group(0)
        wm = re.search(r'\bwidth="(\d+)"', tag)
        hm = re.search(r'\bheight="(\d+)"', tag)
        w = int(wm.group(1)) if wm else 59528
        h = int(hm.group(1)) if hm else 84186
        if size:
            sw, sh = _PAGE_SIZES_MM[size.lower()]
            w, h = _mm_to_hu(sw), _mm_to_hu(sh)
        if width_mm is not None:
            w = _mm_to_hu(width_mm)
        if height_mm is not None:
            h = _mm_to_hu(height_mm)
        if orientation:
            land = orientation.lower() == "landscape"
            if (land and w < h) or (not land and w > h):
                w, h = h, w
        tag = _set_open_attr(tag, "width", str(w))
        tag = _set_open_attr(tag, "height", str(h))
        cnt[0] += 1
        return tag

    new = re.sub(r'<hp:pagePr\b[^>]*?>', repl, xml)
    if margin_mm is not None and cnt[0]:
        g = _mm_to_hu(margin_mm)
        new = re.sub(r'<hp:margin\b[^>]*?/>',
                     lambda mm: _margin_repl(mm.group(0), g), new)
    return new, cnt[0]


def set_page_hwpx(src, dst, orientation=None, margin_mm=None, size=None,
                  width_mm=None, height_mm=None):
    """모든 섹션의 secPr 편집 용지(크기/방향/여백) 변경. 자식 구조는 보존.

    방향은 너비-높이 대소로 결정(landscape면 너비>높이가 되도록 swap). HWPX
    pagePr의 landscape enum 힌트는 claw 검증 동작을 따라 그대로 둔다.
    """
    if orientation and orientation.lower() not in ("portrait", "landscape"):
        raise ValueError("--orientation은 portrait/landscape 중 하나")
    if size and size.lower() not in _PAGE_SIZES_MM:
        raise ValueError("--size는 %s 중 하나" % "/".join(sorted(_PAGE_SIZES_MM)))
    if not (orientation or margin_mm is not None or size
            or width_mm is not None or height_mm is not None):
        raise ValueError("변경 항목(--orientation/--margin-mm/--size/"
                         "--width-mm/--height-mm) 중 하나는 지정해야 합니다")
    buf, names, xmls, _ = _load_doc(src)
    changed, total = {}, 0
    for name in names:
        new_xml, c = _apply_pagepr(
            xmls[name], orientation, margin_mm, size, width_mm, height_mm)
        if c and new_xml != xmls[name]:
            changed[name] = new_xml
        total += c
    if total == 0:
        raise ValueError("set-page: <hp:pagePr>를 찾지 못했습니다 (정상 HWPX 아님)")
    _write_doc(buf, dst, changed)
    return {"action": "set-page", "pagepr_total": total,
            "sections_changed": list(changed),
            "applied": {"orientation": orientation, "margin_mm": margin_mm,
                        "size": size, "width_mm": width_mm,
                        "height_mm": height_mm}}


# ─── 네이티브 차트 삽입 (claw-hwp opInsertChart 포팅) ───────────────
#
# 한컴은 <hp:chart chartIDRef="Chart/chartN.xml">가 가리키는 OOXML chartSpace
# (c:chartSpace) 파트에서 차트를 그린다 — 한컴독스가 함께 쓰는 BinData OLE는
# 렌더링에 필요 없다(claw 검증). 그래서 OOXML chartSpace를 {type,cat,series}로
# 생성해 Chart/ 파트에 넣고, content.hpf 매니페스트에 등록하고, 섹션에 인라인
# (treatAsChar=1) <hp:chart>를 새 문단으로 삽입한다.
#
# 견고히 지원하는 타입: 세로막대(col)/가로막대(bar)/꺾은선(line)/영역(area)/
#   원(pie). 3D·도넛·분산형·방사형 등은 한컴 렌더 검증이 어려워 스코프 제외.
# 색상/제목/데이터레이블 커스터마이즈는 미지원(한컴 기본 팔레트) — limitations.

CHART_DEFAULT_W = 32250   # HWPUNIT (≈113.8mm)
CHART_DEFAULT_H = 18750   # HWPUNIT (≈66.1mm)

_CHART_SPECS = {
    "col":  {"el": "barChart", "dir": "col", "grp": "clustered"},
    "bar":  {"el": "barChart", "dir": "bar", "grp": "clustered"},
    "line": {"el": "lineChart", "grp": "standard", "marker": True},
    "area": {"el": "areaChart", "grp": "standard"},
    "pie":  {"el": "pieChart", "pie": True},
}

_CHART_NS = ('xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
             'relationships" xmlns:a="http://schemas.openxmlformats.org/'
             'drawingml/2006/main" xmlns:c="http://schemas.openxmlformats.org/'
             'drawingml/2006/chart"')


def _chart_col_letter(i):
    return chr(66 + i)  # 0 -> B (A열은 범주축)


def _chart_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f == int(f) else repr(f)


def _chart_str_cache(vals):
    pts = "".join('<c:pt idx="%d"><c:v>%s</c:v></c:pt>'
                  % (i, escape_text(str(v))) for i, v in enumerate(vals))
    return '<c:ptCount val="%d"/>%s' % (len(vals), pts)


def _chart_num_cache(vals):
    pts = "".join('<c:pt idx="%d"><c:v>%s</c:v></c:pt>'
                  % (i, _chart_num(v)) for i, v in enumerate(vals))
    return ('<c:formatCode>General</c:formatCode><c:ptCount val="%d"/>%s'
            % (len(vals), pts))


def _chart_std_ser(idx, name, cat, values):
    """cat+val 계열 (막대/꺾은선/영역/원). 색은 한컴 기본 팔레트(<c:spPr/>)."""
    cl = _chart_col_letter(idx)
    return (
        '<c:ser><c:idx val="%d"/><c:order val="%d"/>'
        '<c:tx><c:strRef><c:f>Sheet1!$%s$1</c:f><c:strCache>'
        '<c:ptCount val="1"/><c:pt idx="0"><c:v>%s</c:v></c:pt>'
        '</c:strCache></c:strRef></c:tx>'
        '<c:spPr/><c:invertIfNegative val="0"/>'
        '<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$%d</c:f>'
        '<c:strCache>%s</c:strCache></c:strRef></c:cat>'
        '<c:val><c:numRef><c:f>Sheet1!$%s$2:$%s$%d</c:f>'
        '<c:numCache>%s</c:numCache></c:numRef></c:val></c:ser>'
        % (idx, idx, cl, escape_text(name), len(cat) + 1, _chart_str_cache(cat),
           cl, cl, len(values) + 1, _chart_num_cache(values)))


def _chart_cat_ax(ax_id, pos, cross):
    return ('<c:catAx><c:axId val="%s"/><c:scaling>'
            '<c:orientation val="minMax"/></c:scaling><c:axPos val="%s"/>'
            '<c:crossAx val="%s"/><c:delete val="0"/>'
            '<c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
            '<c:tickLblPos val="nextTo"/><c:crosses val="autoZero"/>'
            '<c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/>'
            '<c:noMultiLvlLbl val="0"/></c:catAx>' % (ax_id, pos, cross))


def _chart_val_ax(ax_id, pos, cross):
    return ('<c:valAx><c:axId val="%s"/><c:scaling>'
            '<c:orientation val="minMax"/></c:scaling><c:axPos val="%s"/>'
            '<c:majorGridlines/>'
            '<c:numFmt formatCode="General" sourceLinked="1"/>'
            '<c:crossAx val="%s"/><c:delete val="0"/>'
            '<c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
            '<c:tickLblPos val="nextTo"/><c:crosses val="autoZero"/>'
            '<c:crossBetween val="between"/></c:valAx>' % (ax_id, pos, cross))


def _build_chartspace(spec, cat, series):
    """OOXML c:chartSpace 파트 XML 생성 (claw buildChartSpace 포팅)."""
    ax1, ax2 = "111111111", "222222222"
    if spec.get("pie"):
        s0 = series[0]
        plot = ('<c:%s><c:varyColors val="1"/>%s<c:firstSliceAng val="0"/>'
                '</c:%s>'
                % (spec["el"],
                   _chart_std_ser(0, s0["name"], cat, s0["values"]),
                   spec["el"]))
    else:
        sers = "".join(_chart_std_ser(i, s["name"], cat, s["values"])
                       for i, s in enumerate(series))
        horiz = spec.get("dir") == "bar"
        inner = ""
        if spec.get("dir"):
            inner += '<c:barDir val="%s"/>' % spec["dir"]
        if spec.get("grp"):
            inner += '<c:grouping val="%s"/>' % spec["grp"]
        inner += '<c:varyColors val="0"/>' + sers
        if spec.get("marker"):
            inner += '<c:marker val="1"/>'
        if spec["el"].startswith("bar"):
            inner += '<c:gapWidth val="150"/><c:overlap val="0"/>'
        inner += '<c:axId val="%s"/><c:axId val="%s"/>' % (ax1, ax2)
        plot = ('<c:%s>%s</c:%s>%s%s'
                % (spec["el"], inner, spec["el"],
                   _chart_cat_ax(ax1, "l" if horiz else "b", ax2),
                   _chart_val_ax(ax2, "b" if horiz else "l", ax1)))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<c:chartSpace %s><c:date1904 val="0"/>'
            '<c:roundedCorners val="0"/><c:chart>'
            '<c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>%s</c:plotArea>'
            '<c:legend><c:legendPos val="r"/><c:overlay val="0"/></c:legend>'
            '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
            '</c:chart></c:chartSpace>' % (_CHART_NS, plot))


def _chart_object_xml(chart_id, part_name, width, height):
    """인라인(treatAsChar=1) <hp:chart> 봉투 (claw opInsertChart INLINE)."""
    return (
        '<hp:chart id="%d" zOrder="0" numberingType="PICTURE" '
        'textWrap="INLINE" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        'chartIDRef="%s">'
        '<hp:sz width="%d" widthRelTo="ABSOLUTE" height="%d" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" '
        'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
        'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="0" right="0" top="0" bottom="0"/></hp:chart>'
        % (chart_id, part_name, width, height))


def _normalize_chart_data(chart_type, cat, series):
    spec = _CHART_SPECS.get(chart_type)
    if spec is None:
        raise ValueError("--type은 %s 중 하나여야 합니다"
                         % "/".join(sorted(_CHART_SPECS)))
    if not isinstance(cat, list) or not cat:
        raise ValueError("--cat은 비어있지 않은 JSON 배열이어야 합니다")
    cat = [str(c) for c in cat]
    if not isinstance(series, list) or not series:
        raise ValueError("--series는 비어있지 않은 JSON 배열이어야 합니다")
    norm = []
    for i, s in enumerate(series):
        if not isinstance(s, dict):
            raise ValueError("series[%d]는 {name,values} 객체여야 합니다" % i)
        vals = s.get("values", [])
        if not isinstance(vals, list) or not vals:
            raise ValueError("series[%d].values는 비어있지 않은 배열이어야 합니다"
                             % i)
        name = str(s["name"]) if s.get("name") is not None else "계열 %d" % (i + 1)
        norm.append({"name": name, "values": vals})
    if spec.get("pie"):
        norm = [norm[0]]  # 원 차트는 첫 계열만
    return spec, cat, norm


def insert_chart_hwpx(src, dst, chart_type, cat, series, after=None, para=None,
                      width=CHART_DEFAULT_W, height=CHART_DEFAULT_H,
                      section_idx=0):
    """OOXML 차트를 Chart/ 파트에 넣고 매니페스트 등록 + 섹션에 인라인 삽입.

    --after(기준 문구) 또는 --para(문단 인덱스)로 위치 지정. 둘 다 없으면
    해당 섹션 마지막 문단 뒤에 새 PLAIN 문단으로 차트를 붙인다(수식과 동일).
    """
    if width <= 0 or height <= 0:
        raise ValueError("--width/--height는 양의 정수여야 합니다")
    spec, cat, norm_series = _normalize_chart_data(chart_type, cat, series)

    buf, names, xmls, _ = _load_doc(src)
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        namelist = zf.namelist()
        hpf_name = next((n for n in namelist if n.endswith("content.hpf")), None)
        if hpf_name is None:
            raise ValueError("content.hpf(매니페스트)를 찾을 수 없습니다")
        hpf_xml = zf.read(hpf_name).decode("utf-8")

    # 고유 차트 파트명 + 매니페스트 id
    n = 1
    while (("Chart/chart%d.xml" % n) in namelist
           or ('id="chart%d"' % n) in hpf_xml):
        n += 1
    part_name = "Chart/chart%d.xml" % n
    item_id = "chart%d" % n

    chartspace = _build_chartspace(spec, cat, norm_series)

    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    ids = _fresh_ids(sec_xml, 2)
    chart_obj = _chart_object_xml(ids[1], part_name, width, height)
    m = re.search(r'charPrIDRef="(\d+)"', sec_xml[para_el.start:para_el.end])
    char_id = m.group(1) if m else "0"
    new_para = (
        '<hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0"><hp:run charPrIDRef="%s">%s</hp:run></hp:p>'
        % (ids[0], char_id, chart_obj))
    new_sec = apply_splices(sec_xml, [(para_el.end, para_el.end, new_para)])

    item = ('<opf:item id="%s" href="%s" media-type="application/xml"/>'
            % (item_id, part_name))
    if "</opf:manifest>" not in hpf_xml:
        raise ValueError("content.hpf에 <opf:manifest>가 없습니다")
    new_hpf = hpf_xml.replace("</opf:manifest>", item + "</opf:manifest>", 1)

    patched = patch_zip_entries(buf, {
        sec_name: new_sec.encode("utf-8"),
        hpf_name: new_hpf.encode("utf-8"),
    })
    out = add_zip_entries(patched, {part_name: chartspace.encode("utf-8")})
    with open(dst, "wb") as f:
        f.write(out)
    return {"section": sec_name, "chart_part": part_name, "manifest": hpf_name,
            "manifest_id": item_id, "chart_type": chart_type,
            "chart_el": spec["el"], "series": len(norm_series),
            "categories": len(cat)}


# ─── P7: 글머리표·문단번호 목록 in-place 전환 ──────────────────────
#
# 한컴 목록 메커니즘: paraPr 안의 <hh:heading type="BULLET|NUMBER" idRef level/>
# 이 문단의 paraPrIDRef가 가리키는 paraPr에 들어 있어야 한다. BULLET은 idRef가
# <hh:bullets>의 bullet id를, NUMBER는 <hh:numberings>의 numbering id를 가리킨다.
#
# 전략(claw set_bullet_list/set_number_list/clear_list 포팅):
#   1) 목록 정의를 header.xml에 보장(_ensure_bullet_def / _ensure_numbering_def).
#   2) 대상 문단의 현재 paraPr를 복제(_add_cloned)하면서 <hh:heading>을 삽입/교체,
#      문단의 paraPrIDRef를 복제본으로 재지정. 같은 base paraPr를 쓰는 문단들은
#      복제본 하나를 공유(각 문단의 여백/정렬 보존).
#   3) clear-list는 heading 요소를 제거한 복제본으로 재지정.
#
# ★한계(claw mistakes-06): 한컴독스 웹(클라우드 뷰어)은 자기가 만들지 않은
#   합성 list paraPr의 <hh:heading type="BULLET|NUMBER">를 silent strip한다.
#   웹 생존에는 Scripts/ 등 한컴 네이티브 전체 fingerprint 일치가 필요하다.
#   이 구현은 데스크톱 한컴(한/글) 기준으로 목록 렌더링을 보장한다 — limitations 참조.

_BULLET_PARAHEAD = (
    '<hh:paraHead level="0" align="LEFT" useInstWidth="0" autoIndent="1" '
    'widthAdjust="0" textOffsetType="PERCENT" textOffset="50" '
    'numFormat="DIGIT" charPrIDRef="4294967295" checkable="0"/>')


def _id_of(header_xml, el):
    m = re.search(r'\bid="(\d+)"', header_xml[el.start:el.open_end])
    return m.group(1) if m else None


def _ensure_bullet_def(header_xml, char):
    """글머리표(char) 정의를 header.xml의 <hh:bullets>에 보장. (idRef, header)."""
    root = scan_xml(header_xml)
    bullets = next(iter(descendants(root, "bullets")), None)
    if bullets is None:
        block = ('<hh:bullets itemCnt="1"><hh:bullet id="1" char="%s" '
                 'useImage="0">%s</hh:bullet></hh:bullets>'
                 % (escape_text(char), _BULLET_PARAHEAD))
        anchor = header_xml.find('</hh:numberings>')
        if anchor == -1:
            raise ValueError("header.xml에 <hh:numberings>가 없어 "
                             "<hh:bullets> 삽입 위치를 찾지 못함")
        anchor += len('</hh:numberings>')
        return "1", header_xml[:anchor] + block + header_xml[anchor:]
    existing = direct_children(bullets, "bullet")
    for b in existing:
        seg = header_xml[b.start:b.open_end]
        cm = re.search(r'\bchar="([^"]*)"', seg)
        bid = _id_of(header_xml, b)
        if cm and bid and decode_entities(cm.group(1)) == char:
            return bid, header_xml
    ids = [int(_id_of(header_xml, b)) for b in existing if _id_of(header_xml, b)]
    new_id = (max(ids) + 1) if ids else 1
    new_bullet = ('<hh:bullet id="%d" char="%s" useImage="0">%s</hh:bullet>'
                  % (new_id, escape_text(char), _BULLET_PARAHEAD))
    splices = [(bullets.content_end, bullets.content_end, new_bullet)]
    seg = header_xml[bullets.start:bullets.open_end]
    cm = re.search(r'itemCnt="(\d+)"', seg)
    if cm:
        splices.append((bullets.start + cm.start(), bullets.start + cm.end(),
                        'itemCnt="%d"' % (int(cm.group(1)) + 1)))
    return str(new_id), apply_splices(header_xml, splices)


def _numbering_body(style):
    def head(lvl, fmt, text):
        return ('<hh:paraHead start="1" level="%d" align="LEFT" '
                'useInstWidth="1" autoIndent="1" widthAdjust="0" '
                'textOffsetType="PERCENT" textOffset="50" numFormat="%s" '
                'charPrIDRef="4294967295" checkable="0">%s</hh:paraHead>'
                % (lvl, fmt, text))

    def empty(lvl):
        return ('<hh:paraHead start="1" level="%d" align="LEFT" '
                'useInstWidth="1" autoIndent="1" widthAdjust="0" '
                'textOffsetType="PERCENT" textOffset="50" numFormat="DIGIT" '
                'charPrIDRef="4294967295" checkable="0"/>' % lvl)

    if style == "decimal":
        return "".join(
            head(lvl, "DIGIT", "".join("^%d." % (i + 1) for i in range(lvl)))
            for lvl in range(1, 11))
    # korean: 1./가./1)/가)/(1)/(가)
    return (head(1, "DIGIT", "^1.") + head(2, "HANGUL_SYLLABLE", "^2.")
            + head(3, "DIGIT", "^3)") + head(4, "HANGUL_SYLLABLE", "^4)")
            + head(5, "DIGIT", "(^5)") + head(6, "HANGUL_SYLLABLE", "(^6)")
            + empty(7) + empty(8) + empty(9) + empty(10))


def _ensure_numbering_def(header_xml, style):
    """문단번호 정의를 보장. style 미지정 시 기존 numbering 재사용. (idRef, header)."""
    root = scan_xml(header_xml)
    numberings = next(iter(descendants(root, "numberings")), None)
    if numberings is None:
        raise ValueError("header.xml에 <hh:numberings>가 없습니다")
    nums = direct_children(numberings, "numbering")
    if not style:
        if nums:
            return (_id_of(header_xml, nums[0]) or "1"), header_xml
        style = "korean"
    style = str(style).lower()
    if style not in ("korean", "decimal"):
        raise ValueError("style은 korean/decimal 중 하나여야 합니다")
    want = "^1.^2." if style == "decimal" else "^2."
    for n in nums:
        ninner = header_xml[n.content_start:n.content_end]
        m = re.search(r'<hh:paraHead\b[^>]*\blevel="2"[^>]*>([^<]*)</hh:paraHead>',
                      ninner)
        if m and m.group(1) == want:
            return (_id_of(header_xml, n) or "1"), header_xml
    ids = [int(_id_of(header_xml, n)) for n in nums if _id_of(header_xml, n)]
    new_id = (max(ids) + 1) if ids else 1
    new_num = ('<hh:numbering id="%d" start="1">%s</hh:numbering>'
               % (new_id, _numbering_body(style)))
    splices = [(numberings.content_end, numberings.content_end, new_num)]
    seg = header_xml[numberings.start:numberings.open_end]
    cm = re.search(r'itemCnt="(\d+)"', seg)
    if cm:
        splices.append((numberings.start + cm.start(),
                        numberings.start + cm.end(),
                        'itemCnt="%d"' % (int(cm.group(1)) + 1)))
    return str(new_id), apply_splices(header_xml, splices)


def _mutate_list_heading(htype, idref, level):
    """paraPr inner에 <hh:heading>을 삽입/교체(또는 NONE이면 제거)하는 mutate."""
    def m(open_tag, inner):
        if htype == "NONE":
            return open_tag, re.sub(r'<hh:heading\b[^>]*/>', "", inner, count=1)
        heading = ('<hh:heading type="%s" idRef="%s" level="%d"/>'
                   % (htype, idref, int(level)))
        if re.search(r'<hh:heading\b[^>]*/>', inner):
            inner = re.sub(r'<hh:heading\b[^>]*/>',
                           lambda _m: heading, inner, count=1)
        elif re.search(r'<hh:align\b[^>]*/>', inner):
            inner = re.sub(r'<hh:align\b[^>]*/>',
                           lambda mm: mm.group(0) + heading, inner, count=1)
        else:
            inner = heading + inner
        return open_tag, inner
    return m


def _resolve_list_targets(names, xmls, after, para, to_para, section_idx):
    """(section_name, [para_el, ...]) — --after(단일), --para[/--to](범위/단일)."""
    if after is not None:
        n, _x, p = _resolve_target_para(names, xmls, after, None, section_idx)
        return n, [p]
    if section_idx >= len(names):
        raise ValueError("섹션 인덱스 초과: %d" % section_idx)
    n = names[section_idx]
    _, paras = _body_paragraphs(scan_xml(xmls[n]))
    if not paras:
        raise ValueError("섹션에 본문 문단(<hp:p>)이 없습니다")

    def norm(i):
        return i if i >= 0 else len(paras) + i

    if para is None and to_para is None:
        return n, [paras[-1]]
    start = 0 if para is None else norm(para)
    end = start if to_para is None else norm(to_para)
    if start > end:
        start, end = end, start
    if start < 0 or end >= len(paras):
        raise ValueError("문단 인덱스 초과: %d..%d (문단 %d개)"
                         % (start, end, len(paras)))
    return n, paras[start:end + 1]


def set_list_hwpx(src, dst, list_type, after=None, para=None, to_para=None,
                  level=0, char=None, style=None, section_idx=0):
    """대상 본문 문단(들)을 글머리표/문단번호 목록으로 전환하거나 해제."""
    if list_type not in ("bullet", "number", "clear"):
        raise ValueError("list_type은 bullet/number/clear 중 하나")
    buf, names, xmls, header_xml = _load_doc(src)
    if header_xml is None:
        raise ValueError("header.xml이 없습니다")
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        header_name = next((nm for nm in zf.namelist()
                            if _HEADER_XML_RE.search(nm)), None)
    sec_name, targets = _resolve_list_targets(
        names, xmls, after, para, to_para, section_idx)
    sec_xml = xmls[sec_name]

    idref = None
    if list_type == "bullet":
        htype = "BULLET"
        idref, header_xml = _ensure_bullet_def(header_xml, char or "•")
    elif list_type == "number":
        htype = "NUMBER"
        idref, header_xml = _ensure_numbering_def(header_xml, style)
    else:
        htype = "NONE"

    base_to_new = {}
    splices = []
    for p in targets:
        seg = sec_xml[p.start:p.open_end]
        bm = re.search(r'paraPrIDRef="(\d+)"', seg)
        base_pid = bm.group(1) if bm else "0"
        if base_pid not in base_to_new:
            new_id, header_xml = _add_cloned(
                header_xml, "paraProperties", "paraPr", base_pid,
                _mutate_list_heading(htype, idref, level))
            base_to_new[base_pid] = new_id
        new_id = base_to_new[base_pid]
        rm = re.search(r'paraPrIDRef="\d+"', seg)
        if rm:
            splices.append((p.start + rm.start(), p.start + rm.end(),
                            'paraPrIDRef="%s"' % new_id))
        _strip_para_linesegs(sec_xml, p, splices)

    new_sec = apply_splices(sec_xml, splices)
    _write_doc(buf, dst, {header_name: header_xml, sec_name: new_sec})
    return {"action": "set-list", "type": list_type, "section": sec_name,
            "paragraphs": len(targets), "newParaPrIds": list(base_to_new.values()),
            "idRef": idref, "level": int(level),
            "char": (char or "•") if list_type == "bullet" else None,
            "style": style if list_type == "number" else None}


# ─── 도형/글상자 (P11: 사각형 + 글상자, claw insert_shape/textbox 포팅) ──
#
# floating <hp:rect>를 대상 문단(--after/--para) 뒤 새 문단에 삽입. 글상자는
# 같은 rect에 <hp:drawText>(텍스트 한 문단)를 얹은 것. 채움/테두리 색 지정.
# claw buildShape의 rect 봉투/속성을 그대로 따른다(순수 stdlib).

_MATRIX3 = ('<hp:renderingInfo>'
            '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            '</hp:renderingInfo>')


def _rect_xml(xml, w, h, fill, line, text=None, dx=0, dy=0, margin=0,
              line_w=33):
    """floating <hp:rect> (text 있으면 글상자). w/h/dx/dy/margin=HWPUNIT."""
    rid, inst = _fresh_ids(xml, 2)
    wrap = "SQUARE" if text is not None else "IN_FRONT_OF_TEXT"
    draw = ""
    if text is not None:
        pid = _fresh_ids(xml, 1)[0]
        draw = (
            '<hp:drawText lastWidth="4294967295" name="" editable="0">'
            '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
            'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
            'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            '<hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
            'columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>%s'
            '</hp:t></hp:run></hp:p></hp:subList></hp:drawText>'
            % (pid, escape_text(text)))
    return (
        '<hp:rect id="%d" zOrder="0" numberingType="PICTURE" textWrap="%s" '
        'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" '
        'groupLevel="0" instid="%d" ratio="0">'
        '<hp:offset x="0" y="0"/><hp:orgSz width="%d" height="%d"/>'
        '<hp:curSz width="0" height="0"/>'
        '<hp:flip horizontal="0" vertical="0"/>'
        '<hp:rotationInfo angle="0" centerX="0" centerY="0" rotateimage="1"/>'
        '%s'
        '<hp:lineShape color="%s" width="%d" style="SOLID" endCap="FLAT" '
        'headStyle="NORMAL" tailStyle="NORMAL" headfill="1" tailfill="1" '
        'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="NORMAL" '
        'alpha="0"/>'
        '<hc:fillBrush><hc:winBrush faceColor="%s" hatchColor="#000000" '
        'alpha="0"/></hc:fillBrush>'
        '<hp:shadow type="NONE" color="#B2B2B2" offsetX="0" offsetY="0" '
        'alpha="0"/>%s'
        '<hc:pt0 x="0" y="0"/><hc:pt1 x="%d" y="0"/><hc:pt2 x="%d" y="%d"/>'
        '<hc:pt3 x="0" y="%d"/>'
        '<hp:sz width="%d" widthRelTo="ABSOLUTE" height="%d" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="0" '
        'allowOverlap="1" holdAnchorAndSO="0" vertRelTo="PARA" '
        'horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="%d" '
        'horzOffset="%d"/>'
        '<hp:outMargin left="%d" right="%d" top="%d" bottom="%d"/>'
        '<hp:shapeComment>%s</hp:shapeComment></hp:rect>'
        % (rid, wrap, inst, w, h, _MATRIX3, line, line_w, fill, draw,
           w, w, h, h, w, h, dy, dx, margin, margin, margin, margin,
           "글상자" if text is not None else "사각형"))


def _insert_floating_para(src, dst, after, para, section_idx, build_inner):
    """대상 문단 뒤에 floating 개체를 담은 새 문단을 삽입(공통)."""
    buf, names, xmls, _ = _load_doc(src)
    sec_name, sec_xml, para_el = _resolve_target_para(
        names, xmls, after, para, section_idx)
    m = re.search(r'charPrIDRef="(\d+)"', sec_xml[para_el.start:para_el.end])
    char_id = m.group(1) if m else "0"
    inner = build_inner(sec_xml)
    new_para = (
        '<hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0"><hp:run charPrIDRef="%s">%s</hp:run></hp:p>'
        % (_fresh_para_id(sec_xml), char_id, inner))
    new_xml = apply_splices(sec_xml, [(para_el.end, para_el.end, new_para)])
    _write_doc(buf, dst, {sec_name: new_xml})
    return sec_name


def insert_shape_hwpx(src, dst, after=None, para=None, shape="rect",
                      width_mm=50.0, height_mm=20.0, fill="FFFFFF",
                      line="000000", section_idx=0):
    if shape != "rect":
        raise ValueError("현재 shape는 rect만 지원합니다")
    w = max(1, int(round(width_mm * HWPUNIT_PER_MM)))
    h = max(1, int(round(height_mm * HWPUNIT_PER_MM)))
    fc, lc = _norm_hex(fill), _norm_hex(line)
    sec = _insert_floating_para(
        src, dst, after, para, section_idx,
        lambda x: _rect_xml(x, w, h, fc, lc))
    return {"action": "insert-shape", "shape": shape, "section": sec,
            "fill": fc, "line": lc}


def insert_textbox_hwpx(src, dst, text, after=None, para=None,
                        width_mm=60.0, height_mm=25.0, fill="FFFFFF",
                        line="000000", section_idx=0):
    if text is None:
        raise ValueError("--text(글상자 내용)가 필요합니다")
    w = max(1, int(round(width_mm * HWPUNIT_PER_MM)))
    h = max(1, int(round(height_mm * HWPUNIT_PER_MM)))
    fc, lc = _norm_hex(fill), _norm_hex(line)
    sec = _insert_floating_para(
        src, dst, after, para, section_idx,
        lambda x: _rect_xml(x, w, h, fc, lc, text=str(text), margin=567))
    return {"action": "insert-textbox", "section": sec, "text": text,
            "fill": fc, "line": lc}


# ─── 이미지 편집 (P13: 기존 그림 목록/리사이즈/교체/삭제) ──────────────
#
# 문서 순서 hp:pic을 인덱스로 지정해 편집한다. resize는 표시 크기(hp:sz/curSz)만
# 바꿔 모든 포맷 pic에 안전(원본 geometry 보존). replace는 새 이미지를 BinData에
# 넣고 binaryItemIDRef를 repoint(표시 박스 유지). delete는 pic(전용 run이면 run째)
# 제거. 변경 엔트리만 재기록(원본 보존). 인덱스는 list-images로 확인.


def _list_pics(names, xmls):
    """문서 순서 hp:pic 목록: [(section, el, binref, w_hu, h_hu)]."""
    out = []
    for n in names:
        x = xmls[n]
        for pic in descendants(scan_xml(x), "pic"):
            seg = x[pic.start:pic.end]
            br = re.search(r'binaryItemIDRef="([^"]+)"', seg)
            sz = re.search(r'<hp:sz width="(\d+)"[^>]*?\bheight="(\d+)"', seg)
            out.append((n, pic, br.group(1) if br else None,
                        int(sz.group(1)) if sz else 0,
                        int(sz.group(2)) if sz else 0))
    return out


def _pic_at(src, index):
    """(buf, names, xmls, pics, target) — index 검증 포함."""
    buf, names, xmls, _ = _load_doc(src)
    pics = _list_pics(names, xmls)
    if not pics:
        raise ValueError("문서에 이미지(hp:pic)가 없습니다")
    if index < 0 or index >= len(pics):
        raise ValueError("이미지 인덱스 초과: %d (이미지 %d개, 0..%d)"
                         % (index, len(pics), len(pics) - 1))
    return buf, names, xmls, pics, pics[index]


def list_images_hwpx(src):
    buf, names, xmls, _ = _load_doc(src)
    pics = _list_pics(names, xmls)
    items = []
    for i, (n, pic, br, w, h) in enumerate(pics):
        items.append({"index": i, "section": n, "binaryItemIDRef": br,
                      "width_mm": round(w / HWPUNIT_PER_MM, 1),
                      "height_mm": round(h / HWPUNIT_PER_MM, 1)})
    return {"count": len(items), "images": items}


def resize_image_hwpx(src, dst, index, width_mm, height_mm=None):
    buf, names, xmls, pics, (n, pic, br, w0, h0) = _pic_at(src, index)
    w = max(1, int(round(width_mm * HWPUNIT_PER_MM)))
    if height_mm:
        h = max(1, int(round(height_mm * HWPUNIT_PER_MM)))
    elif w0:
        h = max(1, int(round(h0 * (w / w0))))      # 가로세로비 유지
    else:
        h = w
    x = xmls[n]
    seg = x[pic.start:pic.end]
    seg = re.sub(r'(<hp:sz width=")\d+("[^>]*?\bheight=")\d+(")',
                 lambda m: m.group(1) + str(w) + m.group(2) + str(h) + m.group(3),
                 seg, count=1)
    seg = re.sub(r'(<hp:curSz width=")\d+(" height=")\d+(")',
                 lambda m: m.group(1) + str(w) + m.group(2) + str(h) + m.group(3),
                 seg, count=1)
    _write_doc(buf, dst, {n: x[:pic.start] + seg + x[pic.end:]})
    return {"action": "resize-image", "index": index, "section": n,
            "width_mm": round(w / HWPUNIT_PER_MM, 1),
            "height_mm": round(h / HWPUNIT_PER_MM, 1)}


def replace_image_hwpx(src, dst, index, image):
    buf, names, xmls, pics, (n, pic, br, w0, h0) = _pic_at(src, index)
    hpf_name = _find_hpf_name(buf)
    if not hpf_name:
        raise ValueError("content.hpf를 찾을 수 없습니다")
    item_id, entry, ext, data, aspect, nat_w, nat_h = _embed_image(buf, image)
    x = xmls[n]
    seg = re.sub(r'binaryItemIDRef="[^"]+"',
                 'binaryItemIDRef="%s"' % item_id, x[pic.start:pic.end], count=1)
    repl = {n: (x[:pic.start] + seg + x[pic.end:]).encode("utf-8"),
            hpf_name: _register_manifest(buf, hpf_name, item_id, entry, ext)}
    out = add_and_patch_zip(buf, repl, {entry: data})
    with open(dst, "wb") as f:
        f.write(out)
    return {"action": "replace-image", "index": index, "section": n,
            "new_item": item_id, "entry": entry}


def delete_image_hwpx(src, dst, index):
    buf, names, xmls, pics, (n, pic, br, w0, h0) = _pic_at(src, index)
    x = xmls[n]
    run = pic.parent if (pic.parent and pic.parent.name in ("run", "r")) else None
    if run is not None and len(
            [c for c in run.children
             if c.name in ("pic", "t", "ctrl", "tbl", "chart",
                           "equation", "rect", "ellipse", "line")]) == 1:
        a, b = run.start, run.end          # 그림 전용 run → run째 제거
    else:
        a, b = pic.start, pic.end
    _write_doc(buf, dst, {n: x[:a] + x[b:]})
    return {"action": "delete-image", "index": index, "section": n,
            "note": "BinData 항목은 남을 수 있음(참조 끊김, 무해)"}


# ─── 문서 테마 (claw-hwp theme 포팅 — 한국 공문서용 정제 세트) ──────────
#
# 제목/머리 글자색과 표 머리행 배경색을 테마 한 단어로 일괄 적용한다.
# in-place: 기존 .hwpx의 heading charPr(본문보다 큰 글자=제목/머리 휴리스틱)
# textColor를 바꾸고, 각 표 row0 셀 borderFill 배경을 테마색으로(set-cell과
# 동일 메커니즘). 글꼴 변경은 fontface 등록이 필요해 새 문서 생성 경로에서 다룬다.

THEMES = {
    "기본":   {"heading": "#000000", "table_header": "#D9D9D9"},
    "남색":   {"heading": "#1F3864", "table_header": "#D6DCE5"},
    "진녹":   {"heading": "#375623", "table_header": "#E2EFDA"},
    "진회색": {"heading": "#3B3838", "table_header": "#D9D9D9"},
}
_THEME_ALIAS = {"default": "기본", "navy": "남색", "green": "진녹",
                "charcoal": "진회색", "gray": "진회색", "grey": "진회색"}


def set_theme_hwpx(src, dst, theme=None, heading_color=None,
                   table_header_color=None):
    """기존 문서에 테마(제목색·표머리색) in-place 적용."""
    if theme:
        key = _THEME_ALIAS.get(theme.lower(), theme)
        if key not in THEMES:
            raise ValueError("테마는 %s (또는 영문 alias) 중 하나"
                             % "/".join(THEMES))
        heading_color = heading_color or THEMES[key]["heading"]
        table_header_color = table_header_color or THEMES[key]["table_header"]
    if not heading_color and not table_header_color:
        raise ValueError("--theme 또는 --heading-color/--table-header-color 필요")
    buf, names, xmls, header_xml = _load_doc(src)
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        header_name = next((n for n in zf.namelist()
                            if _HEADER_XML_RE.search(n)), None)
    if header_name is None:
        raise ValueError("header.xml이 없습니다")
    changed = {}
    headings = 0

    # 1) 제목/머리 charPr 색 (본문보다 큰 글자 = heading 휴리스틱)
    if heading_color:
        hc = _norm_hex(heading_color)
        used = set()
        for n in names:
            used |= set(re.findall(r'charPrIDRef="(\d+)"', xmls[n]))
        cps = []
        for c in descendants(scan_xml(header_xml), "charPr"):
            seg = header_xml[c.start:c.open_end]
            mid = re.search(r'\bid="(\d+)"', seg)
            mh = re.search(r'\bheight="(\d+)"', seg)
            if mid and mh and mid.group(1) in used:
                cps.append((c, int(mh.group(1))))
        if cps:
            hts = sorted(h for _, h in cps)
            med = hts[len(hts) // 2]
            thresh = max(med * 1.2, med + 100)   # 제목은 본문보다 1.2배+ 큼
            splices = []
            for c, h in cps:
                if h >= thresh:
                    seg = header_xml[c.start:c.open_end]
                    new = _set_open_attr(seg, "textColor", hc)
                    if new != seg:
                        splices.append((c.start, c.open_end, new))
                        headings += 1
            if splices:
                header_xml = apply_splices(header_xml, splices)

    # 2) 표 머리행(row 0) 셀 배경
    cells = 0
    if table_header_color:
        thc = _norm_hex(table_header_color)
        for n in names:
            xml = xmls[n]
            splices = []
            for tbl in descendants(scan_xml(xml), "tbl"):
                rows = direct_children(tbl, "tr")
                if not rows:
                    continue
                for tc in direct_children(rows[0], "tc"):
                    m = re.search(r'borderFillIDRef="(\d+)"',
                                  xml[tc.start:tc.open_end])
                    if not m:
                        continue
                    inner, open_tag = _bf_by_id(header_xml, m.group(1))
                    if inner is None:
                        continue
                    new_id, header_xml = _ensure_borderfill(
                        header_xml, _set_fill_inner(inner, thc), open_tag)
                    if new_id != m.group(1):
                        splices.append((tc.start + m.start(), tc.start + m.end(),
                                        'borderFillIDRef="%s"' % new_id))
                        cells += 1
            if splices:
                xmls[n] = apply_splices(xml, splices)
                changed[n] = xmls[n]

    changed[header_name] = header_xml
    _write_doc(buf, dst, changed)
    return {"action": "theme", "theme": theme, "heading_color": heading_color,
            "table_header_color": table_header_color,
            "headings_recolored": headings, "header_cells_colored": cells}


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

    p_sc = sub.add_parser("set-cell",
                          help="셀 배경색/테두리 설정 (header.xml borderFill)")
    p_sc.add_argument("input")
    p_sc.add_argument("output")
    p_sc.add_argument("--table", type=int, required=True,
                      help="표 인덱스 (해당 섹션 내 문서순서, 중첩표 포함)")
    p_sc.add_argument("--row", type=int, required=True, help="cellAddr rowAddr")
    p_sc.add_argument("--col", type=int, required=True, help="cellAddr colAddr")
    p_sc.add_argument("--bg", help="배경색 RRGGBB (예: FFE600)")
    p_sc.add_argument("--border", choices=["on", "off"],
                      help="4면 테두리 on(SOLID)/off(NONE)")
    p_sc.add_argument("--section", type=int, default=0)

    p_ac = sub.add_parser("add-col", help="표에 열 추가 (끝 또는 --at)")
    p_ac.add_argument("input")
    p_ac.add_argument("output")
    p_ac.add_argument("--table", type=int, required=True)
    p_ac.add_argument("--at", type=int,
                      help="삽입할 colAddr 위치 (생략 시 표 끝에 추가)")
    p_ac.add_argument("--cells",
                      help='새 열 셀 값 ["행0","행1",...] JSON (위→아래)')
    p_ac.add_argument("--section", type=int, default=0)

    p_dr = sub.add_parser("del-row", help="표 행 삭제")
    p_dr.add_argument("input")
    p_dr.add_argument("output")
    p_dr.add_argument("--table", type=int, required=True)
    p_dr.add_argument("--row", type=int, required=True, help="삭제할 행 인덱스")
    p_dr.add_argument("--section", type=int, default=0)

    p_mc = sub.add_parser("merge-cells", help="사각범위 셀 병합")
    p_mc.add_argument("input")
    p_mc.add_argument("output")
    p_mc.add_argument("--table", type=int, required=True)
    p_mc.add_argument("--row", type=int, required=True, help="앵커 rowAddr")
    p_mc.add_argument("--col", type=int, required=True, help="앵커 colAddr")
    p_mc.add_argument("--row2", type=int, required=True, help="범위 끝 rowAddr")
    p_mc.add_argument("--col2", type=int, required=True, help="범위 끝 colAddr")
    p_mc.add_argument("--section", type=int, default=0)

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

    p_eq = sub.add_parser("add-equation",
                          help="네이티브 수식 삽입 (본문 --after 또는 셀 좌표)")
    p_eq.add_argument("input")
    p_eq.add_argument("output")
    p_eq.add_argument("--script", required=True,
                      help="수식 문자열 (예: x^2+y^2=z^2). "
                           "문법은 references/equation-syntax.md 참고")
    p_eq.add_argument("--after",
                      help="기준 문구 (이 문구가 있는 본문 문단 뒤에 새 문단으로)")
    p_eq.add_argument("--table", type=int, help="표 인덱스 (셀에 삽입)")
    p_eq.add_argument("--row", type=int, help="행 인덱스 (cellAddr rowAddr)")
    p_eq.add_argument("--col", type=int, help="열 인덱스 (cellAddr colAddr)")
    p_eq.add_argument("--size", type=int,
                      help="폰트 크기 baseUnit (1000≈10pt, 기본 1000)")
    p_eq.add_argument("--section", type=int, default=0)

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

    p_ts = sub.add_parser("set-text-style",
                          help="글자모양 적용(굵게/기울임/밑줄/색/크기)")
    p_ts.add_argument("input")
    p_ts.add_argument("output")
    p_ts.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
    p_ts.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막). "
                                     "--after/--para 미지정 시 마지막 문단")
    p_ts.add_argument("--bold", action="store_true", help="굵게")
    p_ts.add_argument("--italic", action="store_true", help="기울임")
    p_ts.add_argument("--underline", action="store_true", help="밑줄")
    p_ts.add_argument("--color", help="글자색 RRGGBB (예: FF0000)")
    p_ts.add_argument("--size", type=float, help="글자 크기(pt)")
    p_ts.add_argument("--section", type=int, default=0)

    p_ps = sub.add_parser("set-para-style", help="문단모양 적용(정렬/줄간격)")
    p_ps.add_argument("input")
    p_ps.add_argument("output")
    p_ps.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
    p_ps.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막)")
    p_ps.add_argument("--align",
                      choices=["left", "center", "right", "justify", "both"],
                      help="가로 정렬")
    p_ps.add_argument("--line-spacing", type=int, dest="line_spacing",
                      help="줄간격 퍼센트 (예: 160)")
    p_ps.add_argument("--section", type=int, default=0)

    for _cmd, _h in (("add-footnote", "각주(footNote) 삽입"),
                     ("add-endnote", "미주(endNote) 삽입")):
        _p = sub.add_parser(_cmd, help=_h)
        _p.add_argument("input")
        _p.add_argument("output")
        _p.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
        _p.add_argument("--para",
                        help="문단 인덱스(0-base, last/-1=마지막). "
                             "--after/--para 미지정 시 마지막 문단")
        _p.add_argument("--text", required=True, help="주석 내용")
        _p.add_argument("--section", type=int, default=0)

    p_hl = sub.add_parser("add-hyperlink",
                          help="클릭 가능한 URL 하이퍼링크 삽입")
    p_hl.add_argument("input")
    p_hl.add_argument("output")
    p_hl.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
    p_hl.add_argument("--para",
                      help="문단 인덱스(0-base, last/-1=마지막)")
    p_hl.add_argument("--url", required=True, help="링크 주소")
    p_hl.add_argument("--text", required=True, help="표시 문구")
    p_hl.add_argument("--section", type=int, default=0)

    p_bm = sub.add_parser("add-bookmark", help="책갈피(bookmark) 마커 삽입")
    p_bm.add_argument("input")
    p_bm.add_argument("output")
    p_bm.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
    p_bm.add_argument("--para",
                      help="문단 인덱스(0-base, last/-1=마지막)")
    p_bm.add_argument("--name", required=True, help="책갈피 이름")
    p_bm.add_argument("--section", type=int, default=0)

    # ── P9: 페이지 설정·다단·쪽/단 나누기 ──
    p_pb = sub.add_parser("page-break", help="대상 문단에 쪽 나누기(pageBreak) 설정")
    p_pb.add_argument("input")
    p_pb.add_argument("output")
    p_pb.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
    p_pb.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막). "
                                     "--after/--para 미지정 시 마지막 문단")
    p_pb.add_argument("--off", action="store_true", help="쪽 나누기 해제(0)")
    p_pb.add_argument("--section", type=int, default=0)

    p_cb = sub.add_parser("column-break", help="대상 문단에 단 나누기(columnBreak) 설정")
    p_cb.add_argument("input")
    p_cb.add_argument("output")
    p_cb.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
    p_cb.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막)")
    p_cb.add_argument("--off", action="store_true", help="단 나누기 해제(0)")
    p_cb.add_argument("--section", type=int, default=0)

    p_sc = sub.add_parser("set-columns", help="다단(단/칼럼) 설정. count=1=단일")
    p_sc.add_argument("input")
    p_sc.add_argument("output")
    p_sc.add_argument("--count", type=int, required=True, help="단 수(1 이상)")
    p_sc.add_argument("--gap-mm", type=float, dest="gap_mm",
                      help="단 사이 간격(mm, count>1일 때, 기본≈4mm)")

    p_sp = sub.add_parser("set-page", help="편집 용지(크기/방향/여백) 변경")
    p_sp.add_argument("input")
    p_sp.add_argument("output")
    p_sp.add_argument("--orientation", choices=["portrait", "landscape"],
                      help="용지 방향")
    p_sp.add_argument("--margin-mm", type=float, dest="margin_mm",
                      help="상하좌우 여백(mm)")
    p_sp.add_argument("--size", help="용지 프리셋 (a3/a4/a5/b4/b5/b6/letter/legal)")
    p_sp.add_argument("--width-mm", type=float, dest="width_mm",
                      help="용지 너비(mm) — size보다 우선")
    p_sp.add_argument("--height-mm", type=float, dest="height_mm",
                      help="용지 높이(mm) — size보다 우선")

    p_ic = sub.add_parser("insert-chart",
                          help="네이티브 차트 삽입 (OOXML chartSpace 파트)")
    p_ic.add_argument("input")
    p_ic.add_argument("output")
    p_ic.add_argument("--type", required=True, dest="chart_type",
                      choices=["col", "bar", "line", "area", "pie"],
                      help="차트 종류 (세로막대/가로막대/꺾은선/영역/원)")
    p_ic.add_argument("--cat", required=True,
                      help='범주 라벨 JSON 배열 파일 (예: ["1월","2월","3월"])')
    p_ic.add_argument("--series", required=True,
                      help='계열 JSON [{"name","values":[..]}] 파일')
    p_ic.add_argument("--after",
                      help="기준 문구 (이 문구가 든 본문 문단 뒤에 삽입)")
    p_ic.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막). "
                                     "--after/--para 미지정 시 마지막 문단 뒤")
    p_ic.add_argument("--width", type=int, default=CHART_DEFAULT_W,
                      help="너비 HWPUNIT (기본 %d)" % CHART_DEFAULT_W)
    p_ic.add_argument("--height", type=int, default=CHART_DEFAULT_H,
                      help="높이 HWPUNIT (기본 %d)" % CHART_DEFAULT_H)
    p_ic.add_argument("--section", type=int, default=0)

    for _cmd, _h in (("set-bullet-list", "문단을 글머리표(•) 목록으로 전환"),
                     ("set-number-list", "문단을 번호목록(1. 2. 3.)으로 전환"),
                     ("clear-list", "문단의 목록 서식 해제")):
        _p = sub.add_parser(_cmd, help=_h)
        _p.add_argument("input")
        _p.add_argument("output")
        _p.add_argument("--after", help="기준 문구(이 문구가 든 문단 대상)")
        _p.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막). "
                                       "미지정 시 마지막 문단")
        _p.add_argument("--to", dest="to_para",
                        help="범위 끝 문단 인덱스(--para부터 여기까지 포함)")
        _p.add_argument("--level", type=int, default=0, help="목록 수준(0-base)")
        _p.add_argument("--section", type=int, default=0)
        if _cmd == "set-bullet-list":
            _p.add_argument("--char", help="글머리표 문자(기본 •, 예: ▶ ◯ □ ★)")
        if _cmd == "set-number-list":
            _p.add_argument("--style", choices=["korean", "decimal"],
                            help="번호 형식 (korean: 1./가./1) · decimal: 1./1.1.)")

    p_seal = sub.add_parser(
        "place-seal", help="기준 문구 옆에 떠있는 직인/서명 그림 삽입")
    p_seal.add_argument("input")
    p_seal.add_argument("output")
    p_seal.add_argument("--image", required=True,
                        help="직인/서명 PNG·JPG 경로(사용자 제공)")
    p_seal.add_argument("--anchor", required=True,
                        help='기준 문구(예: 발신명의·"서명 또는 인")')
    p_seal.add_argument("--size-mm", type=float, default=SEAL_DEFAULT_MM,
                        dest="size_mm", help="직인 세로 크기 mm (기본 20)")
    p_seal.add_argument("--dx-mm", type=float, default=0.0, dest="dx_mm",
                        help="가로 미세조정 mm (오른쪽+)")
    p_seal.add_argument("--dy-mm", type=float, default=0.0, dest="dy_mm",
                        help="세로 미세조정 mm (아래+)")
    p_seal.add_argument("--occurrence", type=int, default=0,
                        help="같은 문구가 여러 곳이면 몇 번째(0-base)")
    p_seal.add_argument("--overlap", action="store_true",
                        help="도장처럼 앵커 글자 위에 겹쳐 찍기(기본은 옆)")

    p_img = sub.add_parser(
        "insert-image", help="일반 이미지 삽입 (블록 또는 --inline)")
    p_img.add_argument("input")
    p_img.add_argument("output")
    p_img.add_argument("--image", required=True, help="이미지 PNG·JPG 경로")
    p_img.add_argument("--after", help="기준 문구(이 문단 뒤/끝에 삽입)")
    p_img.add_argument("--para", type=int,
                       help="문단 인덱스(0-base, 음수=뒤에서)")
    p_img.add_argument("--inline", action="store_true",
                       help="기준 문단 끝에 글자처럼 삽입(기본은 새 문단 블록)")
    p_img.add_argument("--size-mm", type=float, nargs="+", dest="size_mm",
                       help="크기 mm: 폭만 또는 '폭 높이'. 미지정 시 원본 크기")
    p_img.add_argument("--section", type=int, default=0)

    p_th = sub.add_parser("set-theme",
                          help="문서 테마(제목색·표머리색) in-place 적용")
    p_th.add_argument("input")
    p_th.add_argument("output")
    p_th.add_argument("--theme", help="기본/남색/진녹/진회색 (또는 default/navy/green/charcoal)")
    p_th.add_argument("--heading-color", dest="heading_color",
                      help="제목/머리 글자색 RRGGBB (테마 override)")
    p_th.add_argument("--table-header-color", dest="table_header_color",
                      help="표 머리행 배경색 RRGGBB (테마 override)")

    p_li = sub.add_parser("list-images", help="문서 내 이미지 목록(인덱스/크기)")
    p_li.add_argument("input")

    p_ri = sub.add_parser("resize-image", help="이미지 크기 변경(표시 크기)")
    p_ri.add_argument("input")
    p_ri.add_argument("output")
    p_ri.add_argument("--index", type=int, required=True, help="이미지 인덱스(list-images)")
    p_ri.add_argument("--width-mm", type=float, required=True, dest="width_mm")
    p_ri.add_argument("--height-mm", type=float, dest="height_mm",
                      help="생략 시 가로세로비 유지")

    p_rp = sub.add_parser("replace-image", help="이미지 교체(BinData 새 항목)")
    p_rp.add_argument("input")
    p_rp.add_argument("output")
    p_rp.add_argument("--index", type=int, required=True)
    p_rp.add_argument("--image", required=True, help="새 이미지 PNG/JPG 경로")

    p_di = sub.add_parser("delete-image", help="이미지 삭제")
    p_di.add_argument("input")
    p_di.add_argument("output")
    p_di.add_argument("--index", type=int, required=True)

    for _cmd, _h in (("insert-shape", "사각형 도형 삽입"),
                     ("insert-textbox", "글상자 삽입")):
        _p = sub.add_parser(_cmd, help=_h)
        _p.add_argument("input")
        _p.add_argument("output")
        _p.add_argument("--after", help="기준 문구(이 문단 뒤에 삽입)")
        _p.add_argument("--para", help="문단 인덱스(0-base, last/-1=마지막)")
        _p.add_argument("--width-mm", type=float, dest="width_mm")
        _p.add_argument("--height-mm", type=float, dest="height_mm")
        _p.add_argument("--fill", default="FFFFFF", help="채움색 RRGGBB")
        _p.add_argument("--line", default="000000", help="테두리색 RRGGBB")
        _p.add_argument("--section", type=int, default=0)
        if _cmd == "insert-textbox":
            _p.add_argument("--text", required=True, help="글상자 내용")

    args = parser.parse_args()

    def _parse_para(v):
        if v is None:
            return None
        return -1 if str(v).lower() == "last" else int(v)

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

        if args.command == "set-cell":
            border = (None if args.border is None
                      else args.border == "on")
            info = set_cell_style_hwpx(args.input, args.output, args.table,
                                       args.row, args.col, args.bg, border,
                                       args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "add-col":
            cells_values = load_values(args.cells) if args.cells else None
            if cells_values is not None and not isinstance(cells_values, list):
                print("오류: cells는 JSON 배열이어야 합니다", file=sys.stderr)
                return 1
            name, _ = _table_op_doc(
                args.input, args.output, args.section,
                lambda x: add_table_column(x, args.table, cells_values,
                                           args.at))
            _print({"input": args.input, "output": args.output,
                    "table": args.table, "at": args.at,
                    "modified_entries": [name], "ok": True})
            return 0

        if args.command == "del-row":
            name, _ = _table_op_doc(
                args.input, args.output, args.section,
                lambda x: delete_table_row(x, args.table, args.row))
            _print({"input": args.input, "output": args.output,
                    "table": args.table, "row": args.row,
                    "modified_entries": [name], "ok": True})
            return 0

        if args.command == "merge-cells":
            name, removed = _table_op_doc(
                args.input, args.output, args.section,
                lambda x: merge_table_cells(x, args.table, args.row, args.col,
                                            args.row2, args.col2))
            _print({"input": args.input, "output": args.output,
                    "table": args.table,
                    "anchor": {"row": args.row, "col": args.col},
                    "to": {"row": args.row2, "col": args.col2},
                    "removed_cells": removed,
                    "modified_entries": [name], "ok": True})
            return 0

        if args.command in ("set-header", "set-footer"):
            kind = "header" if args.command == "set-header" else "footer"
            info = set_header_footer_hwpx(args.input, args.output, kind,
                                          args.text, args.apply, args.align)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "add-equation":
            base_unit = (args.size if args.size is not None
                         else EQUATION_DEFAULT_BASE_UNIT)
            name, where = add_equation_hwpx(
                args.input, args.output, args.script,
                after=args.after, table=args.table, row=args.row,
                col=args.col, base_unit=base_unit, section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    "script": args.script, **where,
                    "modified_entries": [name], "ok": True})
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

        if args.command == "set-text-style":
            info = set_text_style_hwpx(
                args.input, args.output, after=args.after,
                para=_parse_para(args.para), bold=args.bold, italic=args.italic,
                underline=args.underline, color=args.color, size_pt=args.size,
                section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "set-para-style":
            info = set_para_style_hwpx(
                args.input, args.output, after=args.after,
                para=_parse_para(args.para), align=args.align,
                line_spacing=args.line_spacing, section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "place-seal":
            info = place_seal_hwpx(
                args.input, args.output, image=args.image, anchor=args.anchor,
                size_mm=args.size_mm, dx_mm=args.dx_mm, dy_mm=args.dy_mm,
                occurrence=args.occurrence, overlap=args.overlap)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "set-theme":
            info = set_theme_hwpx(
                args.input, args.output, theme=args.theme,
                heading_color=args.heading_color,
                table_header_color=args.table_header_color)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "list-images":
            _print({"input": args.input, **list_images_hwpx(args.input),
                    "ok": True})
            return 0

        if args.command == "resize-image":
            info = resize_image_hwpx(args.input, args.output, args.index,
                                     args.width_mm, args.height_mm)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "replace-image":
            info = replace_image_hwpx(args.input, args.output, args.index,
                                      args.image)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "delete-image":
            info = delete_image_hwpx(args.input, args.output, args.index)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "insert-shape":
            info = insert_shape_hwpx(
                args.input, args.output, after=args.after,
                para=_parse_para(args.para),
                width_mm=args.width_mm if args.width_mm is not None else 50.0,
                height_mm=args.height_mm if args.height_mm is not None else 20.0,
                fill=args.fill, line=args.line, section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "insert-textbox":
            info = insert_textbox_hwpx(
                args.input, args.output, args.text, after=args.after,
                para=_parse_para(args.para),
                width_mm=args.width_mm if args.width_mm is not None else 60.0,
                height_mm=args.height_mm if args.height_mm is not None else 25.0,
                fill=args.fill, line=args.line, section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "insert-image":
            sz = args.size_mm
            if sz is not None and len(sz) > 2:
                raise ValueError("--size-mm은 값 1개(폭) 또는 2개(폭 높이)")
            width_mm = sz[0] if sz else None
            height_mm = sz[1] if sz and len(sz) >= 2 else None
            info = insert_image_hwpx(
                args.input, args.output, image=args.image, after=args.after,
                para=args.para, inline=args.inline, width_mm=width_mm,
                height_mm=height_mm, section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command in ("add-footnote", "add-endnote"):
            kind = "footNote" if args.command == "add-footnote" else "endNote"
            info = add_note_hwpx(
                args.input, args.output, kind, args.text,
                after=args.after, para=_parse_para(args.para),
                section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "add-hyperlink":
            info = add_hyperlink_hwpx(
                args.input, args.output, args.url, args.text,
                after=args.after, para=_parse_para(args.para),
                section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "add-bookmark":
            info = add_bookmark_hwpx(
                args.input, args.output, args.name,
                after=args.after, para=_parse_para(args.para),
                section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command in ("page-break", "column-break"):
            kind = "page" if args.command == "page-break" else "column"
            info = set_para_break_hwpx(
                args.input, args.output, kind, after=args.after,
                para=_parse_para(args.para), on=not args.off,
                section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "set-columns":
            info = set_columns_hwpx(args.input, args.output, args.count,
                                    gap_mm=args.gap_mm)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "set-page":
            info = set_page_hwpx(
                args.input, args.output, orientation=args.orientation,
                margin_mm=args.margin_mm, size=args.size,
                width_mm=args.width_mm, height_mm=args.height_mm)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0

        if args.command == "insert-chart":
            cat = load_values(args.cat)
            series = load_values(args.series)
            info = insert_chart_hwpx(
                args.input, args.output, args.chart_type, cat, series,
                after=args.after, para=_parse_para(args.para),
                width=args.width, height=args.height,
                section_idx=args.section)
            _print({"input": args.input, "output": args.output, **info,
                    "modified_entries": [info["section"], info["manifest"],
                                         info["chart_part"]],
                    "ok": True})
            return 0

        if args.command in ("set-bullet-list", "set-number-list", "clear-list"):
            ltype = {"set-bullet-list": "bullet",
                     "set-number-list": "number",
                     "clear-list": "clear"}[args.command]
            info = set_list_hwpx(
                args.input, args.output, ltype, after=args.after,
                para=_parse_para(args.para), to_para=_parse_para(args.to_para),
                level=args.level, char=getattr(args, "char", None),
                style=getattr(args, "style", None), section_idx=args.section)
            _print({"input": args.input, "output": args.output,
                    **info, "ok": True})
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
