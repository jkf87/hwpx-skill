#!/usr/bin/env python3
"""set-bullet-list / set-number-list / clear-list 회귀 테스트 (P7).

문단을 글머리표·문단번호 목록으로 in-place 전환하고, 원본 본문 보존 +
한컴 열림 게이트(check --strict)를 지키는지 검증.

사용법: python3 tests/test_lists.py
"""
import json
import subprocess
import sys
import tempfile
import zipfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILL = ROOT / "scripts" / "fill_hwpx.py"
BUILD = Path(__file__).resolve().parent / "build_test_form.py"
ASSET = ROOT / "assets" / "report-template.hwpx"

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
    out = json.loads(r.stdout) if r.stdout.strip() else None
    return r.returncode, out


def sec(p):
    return zipfile.ZipFile(p).read("Contents/section0.xml").decode()


def hdr(p):
    return zipfile.ZipFile(p).read("Contents/header.xml").decode()


def body_text(s):
    return "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", s))


def parapr(h, pid):
    m = re.search(r'<hh:paraPr id="%s"[^>]*>.*?</hh:paraPr>' % pid, h, re.S)
    return m.group(0) if m else ""


def well_formed(p):
    import xml.dom.minidom as M
    z = zipfile.ZipFile(p)
    for n in ("Contents/header.xml", "Contents/section0.xml"):
        M.parseString(z.read(n))
    return True


def changed_entries(a, b):
    ca = {i.filename: i.CRC for i in zipfile.ZipFile(a).infolist()}
    cb = {i.filename: i.CRC for i in zipfile.ZipFile(b).infolist()}
    return sorted(n for n in ca if ca[n] != cb.get(n))


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)

        # ── set-bullet-list (test form, --para) ──
        fb = d / "fb.hwpx"
        code, rep = run("set-bullet-list", form, fb, "--para", "1", "--char", "▶")
        check("set-bullet-list 성공", code == 0 and rep and rep["ok"])
        h = hdr(fb)
        check("새 paraPr 생성", bool(rep and rep["newParaPrIds"]))
        check("<hh:bullets> 정의 생성", "<hh:bullets" in h)
        check("커스텀 글머리표 문자 반영", 'char="▶"' in h)
        new_id = rep["newParaPrIds"][0]
        check("복제 paraPr에 BULLET heading",
              'type="BULLET"' in parapr(h, new_id))
        check("대상 문단이 새 paraPr 참조",
              ('paraPrIDRef="%s"' % new_id) in sec(fb))
        check("본문 텍스트 보존", body_text(sec(form)) == body_text(sec(fb)))
        check("XML well-formed", well_formed(fb))
        check("header+section만 변경",
              changed_entries(form, fb) ==
              ["Contents/header.xml", "Contents/section0.xml"])
        code, _ = run("check", fb, "--strict")
        check("bullet 결과 check --strict 통과", code == 0)

        # ── set-number-list (test form, --after, decimal) ──
        fn = d / "fn.hwpx"
        code, rep = run("set-number-list", form, fn,
                        "--after", "입사", "--style", "decimal")
        check("set-number-list 성공", code == 0 and rep and rep["ok"])
        h = hdr(fn)
        nid = rep["newParaPrIds"][0]
        check("복제 paraPr에 NUMBER heading",
              'type="NUMBER"' in parapr(h, nid))
        check("decimal numbering 정의(^1.^2.)", "^1.^2." in h)
        check("본문 텍스트 보존", body_text(sec(form)) == body_text(sec(fn)))
        code, _ = run("check", fn, "--strict")
        check("number 결과 check --strict 통과", code == 0)

        # ── 범위(--para .. --to) on real Hancom asset ──
        ab = d / "asset_bullet.hwpx"
        code, rep = run("set-bullet-list", ASSET, ab, "--para", "0", "--to", "2")
        check("범위 set-bullet-list 성공 (실 한컴 저장본)",
              code == 0 and rep and rep["ok"])
        check("3개 문단 전환", rep and rep["paragraphs"] == 3)
        check("본문 텍스트 보존(asset)",
              body_text(sec(ASSET)) == body_text(sec(ab)))
        check("XML well-formed(asset)", well_formed(ab))
        code, _ = run("check", ab, "--strict")
        check("asset bullet check --strict 통과", code == 0)

        # ── clear-list (bullet 해제) ──
        cl = d / "cleared.hwpx"
        code, rep = run("clear-list", fb, cl, "--para", "1")
        check("clear-list 성공", code == 0 and rep and rep["ok"])
        cid = rep["newParaPrIds"][0]
        check("해제 paraPr에 heading 없음",
              "<hh:heading" not in parapr(hdr(cl), cid))
        check("clear 후 본문 보존", body_text(sec(fb)) == body_text(sec(cl)))
        code, _ = run("check", cl, "--strict")
        check("clear 결과 check --strict 통과", code == 0)

        # ── 인덱스 초과 거부 ──
        code, _ = run("set-bullet-list", form, d / "x.hwpx",
                      "--para", "9999", expect=1)
        check("범위 초과 인덱스 거부(exit 1)", code == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
