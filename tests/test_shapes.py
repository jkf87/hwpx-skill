#!/usr/bin/env python3
"""도형/글상자 (P11) 회귀 테스트 — insert-shape(rect) / insert-textbox.

사용법: python3 tests/test_shapes.py
"""
import json
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILL = ROOT / "scripts" / "fill_hwpx.py"
BUILD = Path(__file__).resolve().parent / "build_test_form.py"

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def run(*args, expect=0):
    r = subprocess.run([sys.executable, str(FILL), *map(str, args)],
                       capture_output=True, text=True)
    out = json.loads(r.stdout) if r.stdout.strip() else None
    return r.returncode, out


def sec(p):
    return zipfile.ZipFile(p).read("Contents/section0.xml").decode()


def crcs(p):
    return {i.filename: i.CRC for i in zipfile.ZipFile(p).infolist()}


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)
        base_rects = sec(form).count("<hp:rect")

        # ── insert-textbox ──
        tb = d / "tb.hwpx"
        code, rep = run("insert-textbox", form, tb, "--para", "last",
                        "--text", "참고 메모", "--fill", "FFF2CC",
                        "--line", "BF9000")
        check("insert-textbox 성공", code == 0 and rep and rep["ok"])
        s = sec(tb)
        check("rect +1 + drawText + 텍스트",
              s.count("<hp:rect") == base_rects + 1
              and "<hp:drawText" in s and "참고 메모" in s)
        check("채움/테두리 색", "#FFF2CC" in s and "#BF9000" in s)
        check("textbox well-formed", _wf(s))
        code, _ = run("check", tb, "--strict")
        check("textbox check --strict", code == 0)

        # ── insert-shape rect ──
        sh = d / "sh.hwpx"
        code, rep = run("insert-shape", form, sh, "--after", "작성자",
                        "--width-mm", "40", "--height-mm", "15",
                        "--fill", "DDEBF7")
        check("insert-shape rect 성공", code == 0 and rep and rep["ok"])
        s2 = sec(sh)
        check("rect +1 (drawText 없음)",
              s2.count("<hp:rect") == base_rects + 1 and "<hp:drawText" not in s2)
        check("도형 채움색", "#DDEBF7" in s2)

        # ── 원본 보존 (section0만) ──
        b, n = crcs(form), crcs(tb)
        check("textbox: section0만 변경",
              sorted(k for k in b if b[k] != n.get(k)) == ["Contents/section0.xml"]
              and not [k for k in n if k not in b])

        # ── 잘못된 색 거부 ──
        code, _ = run("insert-shape", form, d / "x.hwpx", "--para", "0",
                      "--fill", "ZZZ", expect=1)
        check("잘못된 색 거부(exit 1)", code == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def _wf(xml):
    try:
        ET.fromstring(xml)
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
