#!/usr/bin/env python3
"""개조식 보고서 원고(마크다운) 문체 검문기 — references/bogo-munche.md §8 을 도구로.

실측 15건의 통계(서술형 종결 0%, 수사적 대조 0건, 항목 중앙 31자, 결론 30자)를
기준으로 원고의 줄을 검사한다. 내용은 보지 않는다 — 꼴만 본다.

    python3 scripts/munche_lint.py 원고.md            # 사람용 출력
    python3 scripts/munche_lint.py 원고.md --json     # 기계용
종료 코드: 위반(error) 있으면 2, 경고만 있으면 0.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 추상어·가치어 — 'A가 아니라 B' 의 B 가 이런 말이면 수사다
ABSTRACT = ("사람", "마음", "본질", "전환", "방법", "태도", "문화", "철학", "가치", "정신",
            "관계", "신뢰", "과정", "질문", "이야기", "경험", "시간", "공간", "연결", "변화",
            "미래", "시작", "용기", "의지", "선택")
ITEM_MAX = 70        # ❍ 항목 — 실측 중앙 31, 2줄 한계
CONCL_MAX = 60       # ⇒ 결론 — 실측 중앙 30
LEAD_MAX = 140


def strip_md(s: str) -> str:
    s = re.sub(r"[*+!_=]{2}", "", s)
    return s.strip()


def classify(line: str):
    s = line.rstrip()
    t = s.strip()
    if not t:
        return None, ""
    if t.startswith("#"):
        return "head", t.lstrip("#").strip()
    if t.startswith(">"):
        return "lead", t[1:].strip()
    if re.match(r"^(⇒|=>)", t):
        return "concl", re.sub(r"^(⇒|=>)\s*", "", t)
    if t.startswith(("※", "* ")):
        return "note", t[1:].strip()
    if re.match(r"^-\s", t):
        ind = len(s) - len(s.lstrip())
        return ("sub" if ind >= 2 else "item"), re.sub(r"^-\s+", "", t)
    if t.startswith("|") or t == "---" or t.startswith("!["):
        return "skip", t
    return "para", t


DA_END = re.compile(r"(이다|한다|된다|있다|없다|였다|했다|한다|준다|낸다|간다|온다|진다|본다|는다)[\.\s]*$")
DEF_END = re.compile(r"(해야 한다|해야 함|하여야 함|해야 할 것)[\.\s]*$")
NOT_A_BUT = re.compile(r"(가|이|은|는) 아니라\s*([^,\.\s]{1,12})|(가|이) 아닌\s*([^,\.\s]{1,12})")


def lint(text: str) -> dict:
    findings = []
    in_front = False
    _lead_buf: list = []
    for no, raw in enumerate(text.splitlines(), 1):
        if raw.strip() == "---":
            in_front = not in_front if no < 40 else in_front
            continue
        if in_front:
            continue
        kind, body = classify(raw)
        if kind in (None, "skip", "head"):
            continue
        b = strip_md(body)
        add = lambda sev, rule, msg, fix="": findings.append(
            {"line": no, "kind": kind, "severity": sev, "rule": rule, "text": b[:60], "message": msg, "suggest": fix})
        # 1) 서술형 종결
        if kind in ("item", "sub", "concl", "para") and DA_END.search(b) and not b.endswith("있음"):
            add("error", "DA_ENDING", "'~다' 서술형 종결 — 실측 0%", "명사·명사구 또는 '~함/있음' 으로")
        if DEF_END.search(b):
            add("error", "DEONTIC", "'~해야 한다' 당위 종결", "'⇒ ○○ 필요' 처럼 명사로")
        # 2) 수사적 대조
        for m in NOT_A_BUT.finditer(b):
            tail = (m.group(2) or m.group(4) or "")
            if any(w in tail for w in ABSTRACT) or kind == "concl":
                add("error", "RHETORIC_CONTRAST", f"'~가 아니라/아닌 {tail}' — B 가 추상어·가치어면 수사", "두 구체 선택지를 가르는 경우만 허용, 아니면 지움")
            else:
                add("warning", "CONTRAST_CHECK", f"'~가 아니라/아닌 {tail}' — 구체 선택지인지 확인", "")
        # 3) 물음표·느낌표·것이다
        if kind != "note" and re.search(r"[?!？！]", b):
            add("error", "QUESTION_EXCLAIM", "물음표·느낌표 — 본문 0건", "")
        if re.search(r"것이다|것임\b", b):
            add("warning", "GEOSIDA", "'~것이다/것임' — 1건뿐", "명사 종결로")
        # 4) 대구·슬로건 (쉼표로 가른 두 구절이 같은 조사로 끝남)
        if kind in ("item", "para", "lead") and re.search(r"[은는이가]\s[^,]{2,12},\s[^,]{2,12}[은는이가]\s[^,]{2,12}$", b):
            add("warning", "COUPLET", "대구(對句)로 보임 — 슬로건 문장", "목표 수치·명사로")
        if "—" in b and kind in ("item", "sub"):
            add("warning", "EM_DASH", "줄표(—) 로 이어 붙인 설명 — 실측에 없음", "세부 줄로 내리거나 (괄호 소제목)")
        # 5) 길이
        if kind == "item" and len(b) > ITEM_MAX:
            add("warning", "ITEM_LONG", f"❍ 항목 {len(b)}자 — 중앙값 31, 2줄 한계", "세부(- )로 내림")
        if kind == "concl" and len(b) > CONCL_MAX:
            add("warning", "CONCL_LONG", f"⇒ 결론 {len(b)}자 — 중앙값 30", "한 판단만 남김")
        # 6) 리드문 — 연속된 '>' 줄은 한 덩어리. 마지막 줄에서만 종결을 본다
        if kind == "lead":
            nxt = text.splitlines()[no] if no < len(text.splitlines()) else ""
            if not nxt.strip().startswith(">"):
                block = " ".join(strip_md(x.strip()[1:]) for x in _lead_buf + [raw])
                _lead_buf.clear()
                if len(block) > 20 and not block.startswith(("《", "▸", "□", "❍", "○")):
                    if not re.search(r"(고자 함|하려 함|려고 함)\.?$", block):
                        add("warning", "LEAD_ENDING", "리드문은 '~하고자 함.' 한 문장", "'[수단]하고, [수단]하여 [목적]하고자 함.'")
                    if block.count(".") > 1 or len(block) > LEAD_MAX:
                        add("warning", "LEAD_LONG", f"리드문 {len(block)}자·문장 {block.count('.')}개 — 한 문장 40~120자", "")
            else:
                _lead_buf.append(raw)
        # 7) 표기
        if re.search(r"\d{4}년 \d{1,2}월", b):
            add("warning", "DATE_KOREAN", "연·월·일 글자 표기", "2026. 8. 22.")
        if re.search(r"\d{4}\.\d{1,2}\.", b) and not re.search(r"\d{4}\. \d", b):
            add("warning", "DATE_NO_SPACE", "날짜 온점 뒤 띄움 누락", "2026. 8. 22.")
        if re.search(r"\d+천원", b):
            add("warning", "MONEY_CHEON", "'천원' — 실측은 백만원 단위 또는 원", "")
        if re.search(r"\d+,\d{3},\d{3}원", b) and kind in ("item", "sub"):
            add("info", "MONEY_UNIT", "큰 금액은 '○○백만원' 표기가 많음", "")
    sev = {}
    for f in findings:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    return {"findings": findings, "summary": {"total": len(findings), **sev, "ok": sev.get("error", 0) == 0}}


def main() -> int:
    ap = argparse.ArgumentParser(description="개조식 보고서 문체 검문기")
    ap.add_argument("input")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = lint(Path(a.input).read_text(encoding="utf-8"))
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        s = r["summary"]
        print(f"문체 검문: 위반 {s.get('error', 0)}건, 경고 {s.get('warning', 0)}건, 참고 {s.get('info', 0)}건")
        for f in r["findings"]:
            print(f"  L{f['line']:<3} [{f['severity']}] {f['rule']}: \"{f['text']}\" — {f['message']}"
                  + (f" → {f['suggest']}" if f["suggest"] else ""))
    return 0 if r["summary"]["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
