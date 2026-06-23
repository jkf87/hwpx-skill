#!/usr/bin/env python3
"""PreToolUse 훅 — 계획서(gyehoek.py) 생성 전 '제목/목차 포함 여부'를 강제 확인.

계획서를 만들 때는 표지 제목을 넣을지, 목차(순서)를 넣을지 **사용자에게 먼저
물어봐야 한다**. 이 훅은 Bash 도구로 `gyehoek.py`를 실행하려는 명령을 가로채,
제목 결정(--title/--no-title)과 목차 결정(--toc/--no-toc)이 **둘 다 명시되지
않았으면 차단**(exit 2)하고, Claude가 그 사유를 보고 사용자에게 먼저 묻도록 한다.

두 결정이 모두 명시돼 있으면 조용히 통과(exit 0). gyehoek.py 호출이 아니면 통과.

등록 (settings.json > hooks > PreToolUse > matcher "Bash"):
    {"type": "command",
     "command": "python3 /Users/conanssam-m4/hwpx-skill/scripts/gyehoek_hook.py"}

입력: stdin으로 PreToolUse 훅 JSON ({"tool_input": {"command": "..."}, ...})
"""
import json
import os
import re
import shlex
import sys

PYBIN = re.compile(r"^python(\d+(\.\d+)?)?$")


def _args(command):
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _runs_gyehoek(args):
    """python 인터프리터로 gyehoek.py를 **실행**하는 경우만 True.

    파일 인자(git add scripts/gyehoek.py), 커밋 메시지 텍스트, test_gyehoek.py,
    gyehoek_hook.py 등은 오탐하지 않는다."""
    for i, a in enumerate(args):
        if PYBIN.match(os.path.basename(a)):
            for b in args[i + 1:]:
                if b == "-m":            # python -m module → 경로 실행 아님
                    break
                if b.startswith("-"):    # -u 등 인터프리터 플래그는 건너뜀
                    continue
                return os.path.basename(b) == "gyehoek.py"  # 인터프리터 직후 첫 스크립트
    # 직접 실행: ./gyehoek.py
    return bool(args) and os.path.basename(args[0]) == "gyehoek.py"


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # 입력 파싱 실패 시 막지 않음
    command = (data.get("tool_input") or {}).get("command", "")
    if not _runs_gyehoek(_args(command)):
        sys.exit(0)  # gyehoek.py 실행이 아님 → 통과
    args = _args(command)

    has_title = ("--title" in args) or ("--no-title" in args)
    has_toc = ("--toc" in args) or ("--no-toc" in args)
    if has_title and has_toc:
        sys.exit(0)  # 두 결정 모두 명시 → 통과

    missing = []
    if not has_title:
        missing.append("제목(표지) 포함 여부")
    if not has_toc:
        missing.append("목차(순서) 포함 여부")
    sys.stderr.write(
        "[계획서 생성 차단] 계획서를 만들기 전에 다음을 **사용자에게 먼저 질문**해야 합니다: "
        + ", ".join(missing) + ".\n"
        "사용자에게 물어본 뒤, 답에 따라 gyehoek.py를 아래 플래그로 다시 실행하세요:\n"
        "  · 제목 넣음:  --title \"<표지 제목>\"   /  제목 없음:  --no-title\n"
        "  · 목차 넣음:  --toc                    /  목차 없음:  --no-toc\n"
        "예) python3 scripts/gyehoek.py --title \"2026년 ○○ 추진계획\" --toc --output 계획서.hwpx\n"
    )
    sys.exit(2)  # 차단 — Claude가 stderr 사유를 보고 사용자에게 질문


if __name__ == "__main__":
    main()
