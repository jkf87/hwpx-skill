#!/usr/bin/env python3
"""공문서(기안문) 작성법 자동 검수기 — 2025 행정업무운영 편람 기준.

「행정업무의 운영 및 혁신에 관한 규정」 시행규칙 및 2025 행정업무운영 편람
(행정안전부, 2026. 1. 2.)의 표기법을 정규식으로 검사한다. 워크플로우 G의
'검수 모드'를 실제 실행 가능한 도구로 구현한 것.

입력:
    python3 scripts/gonmun_lint.py --hwpx 문서.hwpx
    python3 scripts/gonmun_lint.py --file 본문.txt
    echo "2025.1.6 회의" | python3 scripts/gonmun_lint.py

출력: JSON {findings:[{line, col_text, rule, severity, message, suggest}], summary:{...}}
검사 규칙(고정밀 위주, 오탐 최소화):
    DATE_NO_SPACE   날짜 온점 뒤 공백 누락        2025.1.6  → 2025. 1. 6.
    DATE_NO_END_DOT 날짜 끝 마침표 누락           2025. 1. 6 → 2025. 1. 6.
    DATE_ZERO_PAD   월·일 0 패딩                  2025. 01. 06. → 2025. 1. 6.
    DATE_2DIGIT_YR  2자리 연도                    '24. 1. 6. → 2024. 1. 6.
    TIME_AMPM       오전/오후 + 시               오후 3시 → 15:00
    TIME_24H        24시 표기                     24시 → 익일 00:00 또는 24:00 지양
    TIME_COLON_SP   시:분 사이 공백               13 : 20 → 13:20
    MONEY_CHEONWON  '천원' 표기                   345천원 → 345,000원
    MONEY_GEUM_SP   금과 숫자 사이 공백           금 113,560원 → 금113,560원
    BUNIM_COLON     붙임 뒤 쌍점                  붙임: → 붙임  (2타)
    KKAJI_DUP       물결표 + '까지' 중복          2.20.∼2.24.까지 → 까지 삭제
    FOREIGN_FIRST   외국어 약어 먼저, 한글 괄호   MOU(업무협약) → 업무협약(MOU)
    COLON_SPACE     쌍점 앞 공백/뒤 미띄움        원장 :김갑동 → 원장: 김갑동
"""
import argparse
import html
import json
import re
import sys
import zipfile

# 월/일 한 토큰: 1~12 / 1~31, 0 패딩 검출용
_RULES = []


def rule(code, severity, pattern, message, suggest=None, flags=0):
    _RULES.append((code, severity, re.compile(pattern, flags), message, suggest))


# 날짜 ----------------------------------------------------------------
rule("DATE_NO_SPACE", "error", r"\b\d{4}\.\d{1,2}\.\d{1,2}\.?",
     "날짜 온점 뒤에 한 칸씩 띄워야 함", "예) 2025. 1. 6.")
rule("DATE_ZERO_PAD", "error", r"\b\d{4}\.\s*0\d\.|\.\s*0\d\.",
     "월·일 앞의 '0'은 표기하지 않음", "예) 2025. 1. 6. (2025. 01. 06. ✕)")
rule("DATE_2DIGIT_YR", "error", r"(?<!\d)'\d{2}\.\s*\d",
     "연도는 네 자리로 표기('24 ✕)", "예) 2025. 1. 6.")
rule("DATE_NO_END_DOT", "warning", r"\b\d{4}\.\s\d{1,2}\.\s\d{1,2}(?!\s*[.\d(])",
     "날짜의 '일' 다음에 마침표(.)를 찍어야 함", "예) 2025. 1. 6.")
# 시간 ----------------------------------------------------------------
rule("TIME_AMPM", "error", r"(오전|오후|아침|밤|낮)\s*\d{1,2}\s*시",
     "24시각제 숫자로 표기(오전/오후 사용 안 함)", "예) 09:00, 15:30")
rule("TIME_24H", "warning", r"(?<!\d)24\s*시(?!각)",
     "'24시'보다 익일 00:00 또는 '18:00까지' 권장", "예) 18:00")
rule("TIME_COLON_SP", "error", r"\b\d{1,2}\s+:\s*\d{2}\b|\b\d{1,2}:\s+\d{2}\b",
     "시와 분 사이 쌍점은 양쪽을 붙여 씀", "예) 13:20")
# 금액 ----------------------------------------------------------------
rule("MONEY_CHEONWON", "error", r"\d+\s*천\s*원",
     "금액은 '천원'으로 줄이지 않고 아라비아 숫자로", "예) 345,000원")
rule("MONEY_GEUM_SP", "warning", r"금\s+\d",
     "'금'과 숫자 사이는 붙여 쓰는 것이 원칙", "예) 금113,560원")
# 붙임/끝 -------------------------------------------------------------
rule("BUNIM_COLON", "error", r"붙\s*임\s*:",
     "'붙임' 다음에 쌍점(:)을 붙이지 않음(2타 띄움)", "예) 붙임  계획서 1부.")
# 표기 ----------------------------------------------------------------
rule("KKAJI_DUP", "error", r"[∼~][^\n]{0,20}?까지",
     "물결표(∼)와 '까지'를 함께 쓰지 않음", "예) 2. 20.∼2. 24.")
rule("FOREIGN_FIRST", "warning", r"\b[A-Z]{2,5}\s*\([가-힣]",
     "한글을 먼저 쓰고 괄호 안에 외국어를 병기", "예) 업무 협약(MOU)")
rule("COLON_SPACE", "warning", r"\S\s+:\S|\S:[^\s\d]",
     "쌍점은 앞말에 붙이고 뒤는 한 칸 띄움", "예) 원장: 김갑동")


def extract_text(hwpx_path):
    with zipfile.ZipFile(hwpx_path) as z:
        names = [n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)]
        out = []
        for n in sorted(names):
            x = z.read(n).decode("utf-8", "replace")
            for t in re.findall(r"<hp:t>(.*?)</hp:t>", x, re.DOTALL):
                out.append(html.unescape(re.sub(r"<[^>]+>", "", t)))
        return "\n".join(out)


def lint_text(text):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        for code, severity, rx, message, suggest in _RULES:
            for m in rx.finditer(line):
                findings.append({
                    "line": i, "match": m.group(0).strip(), "rule": code,
                    "severity": severity, "message": message, "suggest": suggest,
                })
    sev = {}
    for f in findings:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    return {"findings": findings,
            "summary": {"total": len(findings), **sev,
                        "ok": sev.get("error", 0) == 0}}


def main():
    ap = argparse.ArgumentParser(description="공문서 작성법 자동 검수기(2025 편람)")
    ap.add_argument("--hwpx", help=".hwpx 파일에서 본문 추출 후 검수")
    ap.add_argument("--file", help="텍스트 파일 검수")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()

    if args.hwpx:
        text = extract_text(args.hwpx)
    elif args.file:
        text = open(args.file, encoding="utf-8").read()
    else:
        text = sys.stdin.read()

    result = lint_text(text)
    if args.format == "text":
        s = result["summary"]
        print(f"검수 결과: 위반 {s['total']}건 (error {s.get('error',0)}, warning {s.get('warning',0)})")
        for f in result["findings"]:
            print(f"  L{f['line']} [{f['severity']}] {f['rule']}: \"{f['match']}\" — {f['message']}"
                  + (f" → {f['suggest']}" if f['suggest'] else ""))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["summary"]["ok"] else 1)


if __name__ == "__main__":
    main()
