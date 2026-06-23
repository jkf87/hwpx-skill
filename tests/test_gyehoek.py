#!/usr/bin/env python3
"""gyehoek.py(공공기관 계획서 생성기) + gyehoek_hook.py(제목/목차 강제질문 훅) 테스트.

사용법: python3 tests/test_gyehoek.py
"""
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gyehoek  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name} {detail}")


print("[reference]")
check("assets/gyehoek-reference.hwpx 존재", gyehoek.REF.exists())
check("기존 체육과 government-reference.hwpx 제거됨",
      not (ROOT / "assets" / "government-reference.hwpx").exists())

have_lxml = False
try:
    import lxml  # noqa: F401
    have_lxml = True
except Exception:
    pass


def gen(**kw):
    d = tempfile.mkdtemp()
    out = Path(d) / "g.hwpx"
    gyehoek.generate(str(out), **kw)
    x = zipfile.ZipFile(out).read("Contents/section0.xml").decode()
    v = c = None
    if have_lxml:
        v = subprocess.run([sys.executable, str(ROOT / "scripts/validate.py"), str(out)],
                           capture_output=True, text=True).stdout
        c = subprocess.run([sys.executable, str(ROOT / "scripts/fill_hwpx.py"),
                            "check", str(out), "--strict"], capture_output=True, text=True).returncode
    return out, x, v, c


print("[generate — 4개 조합 모두 유효해야]")
T = "2026년 ○○도 안전관리 추진계획"
combos = [
    ("제목O목차O", dict(title=T, date="2026. 1.", include_title=True, include_toc=True), True, True),
    ("제목O목차X", dict(title=T, include_title=True, include_toc=False), True, False),
    ("제목X목차O", dict(include_title=False, include_toc=True), False, True),
    ("제목X목차X", dict(include_title=False, include_toc=False), False, False),
]
for name, kw, want_cover, want_toc in combos:
    out, x, v, c = gen(**kw)
    check(f"{name}: 본문 보존", "업무 추진 방향" in x)
    check(f"{name}: 목차(순서) {'포함' if want_toc else '제외'}",
          ("순   서" in x) == want_toc)
    # 표지 제거 시 원 표지제목 사라짐 / 유지+title 시 교체
    if want_cover:
        check(f"{name}: 표지 제목 교체", "안전관리 추진계획" in x and "주요업무 추진계획" not in x)
    else:
        check(f"{name}: 표지 원제목 제거", "주요업무 추진계획" not in x)
    if have_lxml:
        check(f"{name}: validate VALID", v and "VALID" in v and "INVALID" not in v, detail=str(v)[:80])
        check(f"{name}: check --strict 통과", c == 0)

if not have_lxml:
    print("  ⊘ validate/check SKIP (lxml 미설치)")

print("[hook — 제목/목차 미결정 시 차단]")
HOOK = [sys.executable, str(ROOT / "scripts/gyehoek_hook.py")]


def hook(cmd):
    return subprocess.run(HOOK, input=json.dumps({"tool_input": {"command": cmd}}),
                          capture_output=True, text=True).returncode


check("플래그 없음 → 차단(2)", hook("python3 scripts/gyehoek.py -o a.hwpx") == 2)
check("제목O목차O → 통과(0)",
      hook("python3 scripts/gyehoek.py --title '제목' --toc -o a.hwpx") == 0)
check("제목X목차X → 통과(0)",
      hook("python3 scripts/gyehoek.py --no-title --no-toc -o a.hwpx") == 0)
check("목차 결정 없음 → 차단(2)",
      hook("python3 scripts/gyehoek.py --title '제목' -o a.hwpx") == 2)
check("계획서 아님 → 통과(0)", hook("python3 scripts/gonmun.py --sample") == 0)
# 오탐 방지: 파일 인자/텍스트로 gyehoek.py가 등장해도 '실행'이 아니면 통과
check("git add 파일인자 → 통과(0)",
      hook("git add scripts/gyehoek.py scripts/gyehoek_hook.py tests/test_gyehoek.py") == 0)
check("test_gyehoek.py 실행 → 통과(0)", hook("python3 tests/test_gyehoek.py") == 0)
check("cat scripts/gyehoek.py → 통과(0)", hook("cat scripts/gyehoek.py") == 0)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
