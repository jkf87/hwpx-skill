#!/usr/bin/env python3
"""fill_hwpx.py add-equation 스모크 테스트 — 네이티브 수식 삽입(P5).

사용법: python3 tests/test_equation.py
종료 코드 0이면 전체 통과.
"""
import json
import subprocess
import sys
import tempfile
import zipfile
import xml.dom.minidom as minidom
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
    if r.returncode != expect:
        print(f"    [exit {r.returncode}] {r.stderr.strip()[:200]}")
    out = None
    if r.stdout.strip():
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return r.returncode, out


def sec0(path):
    return zipfile.ZipFile(path).read("Contents/section0.xml").decode()


def crc_map(path):
    return {i.filename: i.CRC for i in zipfile.ZipFile(path).infolist()}


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)

        # ── 1) 본문 삽입 (--after) ──────────────────────────────────
        out1 = d / "eq_after.hwpx"
        code, rep = run("add-equation", form, out1,
                        "--after", "입사 지원 신청서",
                        "--script", "x^2+y^2=z^2")
        check("add-equation --after 성공", code == 0 and rep and rep["ok"])
        check("리포트 mode=after", rep and rep.get("mode") == "after")
        s1 = sec0(out1)
        check("<hp:equation> 존재", "<hp:equation" in s1)
        check("<hp:script> 존재", "<hp:script>" in s1)
        check("수식 문자열 보존", "x^2+y^2=z^2" in s1)
        check("treatAsChar 인라인 배치", 'treatAsChar="1"' in s1)
        check("자기완결 봉투 (HancomEQN 내장)", 'font="HancomEQN"' in s1)
        check("section0.xml well-formed",
              _well_formed(s1))

        # ── 2) 셀 삽입 (--table/--row/--col) + --size ───────────────
        out2 = d / "eq_cell.hwpx"
        code, rep = run("add-equation", out1, out2,
                        "--table", 0, "--row", 1, "--col", 1,
                        "--script", "int _0 ^1 x^2 dx = 1 over 3",
                        "--size", 1200)
        check("add-equation 셀 삽입 성공", code == 0 and rep and rep["ok"])
        check("리포트 mode=cell", rep and rep.get("mode") == "cell")
        s2 = sec0(out2)
        check("수식 2개 (본문 + 셀)", s2.count("<hp:equation") == 2)
        check("셀 수식 문자열 보존",
              "int _0 ^1 x^2 dx = 1 over 3" in s2)
        check("--size → baseUnit 반영", 'baseUnit="1200"' in s2)
        check("셀 삽입 후 well-formed", _well_formed(s2))

        # ── 2b) --size 0/음수 거부 ─────────────────────────────────
        out_bad = d / "eq_badsize.hwpx"
        code, _ = run("add-equation", form, out_bad,
                      "--after", "입사 지원 신청서",
                      "--script", "x^2", "--size", 0, expect=1)
        check("--size 0 거부(exit 1)", code == 1)
        check("--size 0 시 출력 미생성", not out_bad.exists())

        # ── 3) 이스케이프 (< > & 가 든 수식) ───────────────────────
        out3 = d / "eq_esc.hwpx"
        code, rep = run("add-equation", form, out3,
                        "--after", "입사 지원 신청서",
                        "--script", "a < b & c > d")
        check("이스케이프 수식 삽입 성공", code == 0 and rep and rep["ok"])
        s3 = sec0(out3)
        check("특수문자 이스케이프됨",
              "a &lt; b &amp; c &gt; d" in s3
              and "a < b & c > d" not in s3)
        check("이스케이프 후 well-formed", _well_formed(s3))

        # ── 4) 원본 보존 — section0.xml만 변경 ─────────────────────
        be, oe = crc_map(form), crc_map(out1)
        changed = [k for k in be if be[k] != oe.get(k)]
        check("원본 보존: section0.xml만 변경",
              changed == ["Contents/section0.xml"]
              and not [k for k in oe if k not in be])

        # ── 5) 한컴 열림 게이트 (check --strict) ───────────────────
        code, rep = run("check", out2, "--strict")
        check("check --strict 통과 (exit 0)",
              code == 0 and rep and rep["ok"])

        # ── 6) 에러 처리 ───────────────────────────────────────────
        out4 = d / "eq_err.hwpx"
        code, _ = run("add-equation", form, out4,
                      "--after", "존재하지않는문구XYZ",
                      "--script", "a^2", expect=1)
        check("기준 문구 없음 → 실패", code == 1)
        code, _ = run("add-equation", form, out4,
                      "--script", "a^2", expect=1)
        check("위치 미지정 → 실패", code == 1)
        code, _ = run("add-equation", form, out4,
                      "--table", 0, "--row", 0, "--col", 0,
                      "--script", "   ", expect=1)
        check("빈 수식 → 실패", code == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def _well_formed(xml):
    try:
        minidom.parseString(xml)
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
