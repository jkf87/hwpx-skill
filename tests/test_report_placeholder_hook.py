#!/usr/bin/env python3
"""report_placeholder_hook.py 스모크 테스트.

'브라더 공기관' placeholder가 남은 보고서를 전달(open/Downloads 복사)하려 하면
차단(exit 2)하고, placeholder 없는 파일·비전달 명령은 통과(exit 0)하는지 검증.

사용법: python3 tests/test_report_placeholder_hook.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "report_placeholder_hook.py"
DIRTY = ROOT / "assets" / "report-template.hwpx"      # '브라더 공기관' 포함
CLEAN = ROOT / "assets" / "gyehoek-reference.hwpx"     # placeholder 없음

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def run(command):
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True)
    return r.returncode


def main():
    check("open 브라더 보고서 → 차단(exit 2)", run(f"open '{DIRTY}'") == 2)
    check("open 깨끗한 파일 → 통과(exit 0)", run(f"open '{CLEAN}'") == 0)
    check("cp 브라더 → Downloads → 차단(exit 2)",
          run(f"cp '{DIRTY}' ~/Downloads/report.hwpx") == 2)
    check("비전달 명령(파이프라인 실행) → 통과(exit 0)",
          run(f"python3 scripts/fill_hwpx.py analyze '{DIRTY}'") == 0)
    check("전달이지만 placeholder 없음 → 통과(exit 0)",
          run(f"cp '{CLEAN}' ~/Downloads/x.hwpx") == 0)
    check("존재하지 않는 .hwpx open → 통과(exit 0)",
          run("open /tmp/none.hwpx") == 0)
    # bad json stdin → 통과
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    check("잘못된 JSON stdin → 통과(exit 0)", r.returncode == 0)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
