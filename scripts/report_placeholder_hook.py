#!/usr/bin/env python3
"""PreToolUse 훅 — '브라더 공기관' 예시 보고서를 실제 보고서로 전달하는 것을 차단.

`assets/report-template.hwpx`는 보고서 워크플로우의 베이스 템플릿이지만, 본문에
'브라더 공기관'이라는 예시 기관명(스킬 원작자 placeholder)이 들어 있다. 이 문구를
실제 기관명으로 바꾸지 않은 채 사용자에게 전달(한컴으로 열기·Downloads/Desktop
복사 등)하면 '브라더 공기관' 보고서가 그대로 나가는 사고가 난다.

이 훅은 Bash 도구로 .hwpx를 '전달'하는 명령을 가로채, 대상 파일 본문에 placeholder
문구가 남아 있으면 차단(exit 2)하고, Claude가 그 사유를 보고 기관명을 먼저
교체하도록 한다(fill_hwpx.py replace로 '브라더 공기관' → 실제 기관명).

placeholder가 없으면 조용히 통과(exit 0). '전달' 명령이 아니면 통과.
템플릿을 작업용으로 복제(비-전달 경로)하는 것은 막지 않는다 — 채운 결과물에는
placeholder가 없으므로 전달 시 통과된다.

등록 (settings.json > hooks > PreToolUse > matcher "Bash"):
    {"type": "command",
     "command": "python3 /Users/conanssam-m4/hwpx-skill/scripts/report_placeholder_hook.py"}

입력: stdin으로 PreToolUse 훅 JSON ({"tool_input": {"command": "..."}, ...})
"""
import json
import os
import re
import sys
import zipfile

# report-template.hwpx의 예시 기관명. 실제 보고서엔 거의 나오지 않는 특정 문구라
# 오탐 위험이 낮다. (단순 '브라더'는 실제 보고서 내용과 겹칠 수 있어 제외)
PLACEHOLDER = "브라더 공기관"

# .hwpx를 '전달'하는 명령으로 보는 패턴 — 이때만 검사 (생성/중간단계는 통과)
DELIVERY_PATTERNS = [r"\bopen\b", r"\bcp\b", r"\bmv\b", r"\brsync\b", r"\bditto\b"]
# 전달 목적지로 보이는 경로 (이 디렉토리로 가는 .hwpx는 사용자 손에 들어감)
DELIVERY_DIRS = ("Downloads", "Desktop", "바탕화면", "다운로드")
HWPX_RE = re.compile(r'([^\s"\'<>|]+\.hwpx)')
_SECTION_RE = re.compile(r"section\d+\.xml$")


def find_target_hwpx(command):
    """명령어에서 '전달'되는 .hwpx 경로를 추출 (없으면 None).

    hwpx_guard_hook과 동일한 휴리스틱: open 계열은 열리는 파일 자체, cp/mv는
    Downloads/Desktop 등 전달 목적지로 갈 때만 검사한다.
    """
    if not any(re.search(p, command) for p in DELIVERY_PATTERNS):
        return None
    candidates = HWPX_RE.findall(command)
    if not candidates:
        return None
    is_delivery_dest = any(dirn in command for dirn in DELIVERY_DIRS)
    if re.search(r"\bopen\b", command) or is_delivery_dest:
        for c in candidates:
            path = os.path.expanduser(c)
            if os.path.isfile(path):
                return path
        return os.path.expanduser(candidates[0])
    return None


def contains_placeholder(path):
    try:
        with zipfile.ZipFile(path) as zf:
            for n in zf.namelist():
                if _SECTION_RE.search(n):
                    if PLACEHOLDER in zf.read(n).decode("utf-8", "replace"):
                        return True
    except Exception:  # noqa: BLE001
        return False  # 열기 실패 시 막지 않음 (다른 훅/게이트가 처리)
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # 입력 파싱 실패 시 통과 (작업 방해 안 함)

    command = (payload.get("tool_input") or {}).get("command", "")
    if not command:
        return 0

    target = find_target_hwpx(command)
    if not target or not os.path.isfile(target):
        return 0
    if not contains_placeholder(target):
        return 0

    base = os.path.basename(target)
    sys.stderr.write(
        "[report-placeholder] 차단: " + base + " 에 예시 기관명 '"
        + PLACEHOLDER + "'(report-template.hwpx placeholder)이 남아 있습니다.\n"
        "실제 보고서로 전달하기 전에 기관명을 교체하세요:\n"
        "  python3 ${CLAUDE_SKILL_DIR}/scripts/fill_hwpx.py replace "
        + base + " out.hwpx --map map.json\n"
        '  (map.json 예: {"' + PLACEHOLDER + '": "<실제 기관명>"})\n'
    )
    return 2  # 차단 — Claude가 stderr 사유를 보고 기관명 교체 후 재전달


if __name__ == "__main__":
    sys.exit(main())
