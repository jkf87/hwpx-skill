#!/usr/bin/env python3
"""set-text-style / set-para-style 회귀 테스트 (P8).

글자모양(charPr)·문단모양(paraPr)을 복제·변형해 대상 문단에 적용하고, 원본
보존 + 한컴 열림 게이트를 지키는지 검증.

사용법: python3 tests/test_text_para_style.py
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


def itemcnt(h, tag):
    m = re.search(r'<hh:%s[^>]*itemCnt="(\d+)"' % tag, h)
    return int(m.group(1)) if m else -1


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)

        # ── set-text-style ──
        ts = d / "ts.hwpx"
        code, rep = run("set-text-style", form, ts, "--after", "입사 지원 신청서",
                        "--bold", "--color", "C00000", "--size", "16")
        check("set-text-style 성공", code == 0 and rep and rep["ok"])
        h = hdr(ts)
        cid = rep["charPrId"]
        cpr = re.search(r'<hh:charPr id="%s".*?</hh:charPr>' % cid, h, re.S)
        check("새 charPr 생성", bool(cpr))
        block = cpr.group(0) if cpr else ""
        check("bold + 색 + 크기 반영",
              "<hh:bold/>" in block and 'textColor="#C00000"' in block
              and 'height="1600"' in block)
        check("bold가 <hh:underline> 앞",
              block.find("<hh:bold/>") < block.find("<hh:underline"))
        check("charProperties itemCnt 증가",
              itemcnt(h, "charProperties") > itemcnt(hdr(form), "charProperties"))
        check("대상 run이 새 charPr 참조",
              ('charPrIDRef="%s"' % cid) in sec(ts))

        # ── set-para-style ──
        ps = d / "ps.hwpx"
        code, rep = run("set-para-style", ts, ps, "--after", "입사 지원 신청서",
                        "--align", "center", "--line-spacing", "180")
        check("set-para-style 성공", code == 0 and rep and rep["ok"])
        h = hdr(ps)
        pid = rep["paraPrId"]
        ppr = re.search(r'<hh:paraPr id="%s".*?</hh:paraPr>' % pid, h, re.S)
        check("새 paraPr 생성", bool(ppr))
        pblock = ppr.group(0) if ppr else ""
        check("정렬 CENTER + 줄간격 180 반영",
              'horizontal="CENTER"' in pblock
              and 'value="180"' in pblock)
        check("대상 문단이 새 paraPr 참조",
              ('paraPrIDRef="%s"' % pid) in sec(ps))

        # ── 원본 보존: header + section만 변경 ──
        bm = {i.filename: i.CRC for i in zipfile.ZipFile(form).infolist()}
        pm = {i.filename: i.CRC for i in zipfile.ZipFile(ps).infolist()}
        changed = sorted(k for k in bm if bm[k] != pm.get(k))
        check("header+section만 변경",
              changed == ["Contents/header.xml", "Contents/section0.xml"]
              and not [k for k in pm if k not in bm])

        # ── check --strict ──
        code, _ = run("check", ps, "--strict")
        check("최종본 check --strict 통과", code == 0)

        # ── --para last 기본 + 스타일 누락 거부 ──
        last = d / "last.hwpx"
        code, _ = run("set-text-style", form, last, "--para", "last", "--italic")
        check("--para last 동작", code == 0 and last.is_file())
        code, _ = run("set-text-style", form, d / "x.hwpx", "--after", "입사 지원 신청서",
                      expect=1)
        check("스타일 미지정 시 거부(exit 1)", code == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
