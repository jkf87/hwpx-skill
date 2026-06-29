#!/usr/bin/env python3
"""각주·미주·하이퍼링크·책갈피 회귀 테스트 (P4).

대상 본문 문단에 각주(hp:footNote)/미주(hp:endNote)/하이퍼링크(HYPERLINK
필드)/책갈피(hp:bookmark) 컨트롤을 주입하고, 본문 보존 + XML well-formed +
한컴 열림 게이트(check --strict)를 지키는지 검증.

사용법: python3 tests/test_notes_links.py
"""
import json
import subprocess
import sys
import tempfile
import xml.dom.minidom as minidom
import zipfile
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


def wellformed(p):
    try:
        minidom.parseString(zipfile.ZipFile(p).read("Contents/section0.xml"))
        return True
    except Exception:
        return False


def crc_map(p):
    return {i.filename: i.CRC for i in zipfile.ZipFile(p).infolist()}


def changed_entries(a, b):
    am, bm = crc_map(a), crc_map(b)
    return sorted(k for k in am if am[k] != bm.get(k)), \
        [k for k in bm if k not in am]


ANCHOR = "입사 지원 신청서"


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)

        # ── 각주 ──
        fn = d / "fn.hwpx"
        code, rep = run("add-footnote", form, fn, "--after", ANCHOR,
                        "--text", "각주 내용입니다")
        check("add-footnote 성공", code == 0 and rep and rep["ok"])
        s = sec(fn)
        check("hp:footNote 컨트롤 생성", "<hp:footNote" in s)
        check("각주 텍스트 포함", "각주 내용입니다" in s)
        check("각주: subList 봉투 포함", "<hp:subList" in s)
        check("각주: 본문 보존", ANCHOR in s)
        check("각주: section0만 변경",
              changed_entries(form, fn) == (["Contents/section0.xml"], []))
        check("각주: well-formed", wellformed(fn))
        code, _ = run("check", fn, "--strict")
        check("각주: check --strict 통과", code == 0)

        # ── 미주 ──
        en = d / "en.hwpx"
        code, rep = run("add-endnote", form, en, "--para", "last",
                        "--text", "미주 내용")
        check("add-endnote 성공", code == 0 and rep and rep["ok"])
        s = sec(en)
        check("hp:endNote 컨트롤 생성", "<hp:endNote" in s)
        check("미주 텍스트 포함", "미주 내용" in s)
        check("미주: section0만 변경",
              changed_entries(form, en) == (["Contents/section0.xml"], []))
        check("미주: well-formed", wellformed(en))
        code, _ = run("check", en, "--strict")
        check("미주: check --strict 통과", code == 0)

        # ── 하이퍼링크 ──
        hl = d / "hl.hwpx"
        code, rep = run("add-hyperlink", form, hl, "--after", ANCHOR,
                        "--url", "https://example.com/path?q=1&x=2",
                        "--text", "예시 링크")
        check("add-hyperlink 성공", code == 0 and rep and rep["ok"])
        s = sec(hl)
        check("HYPERLINK 필드 생성",
              '<hp:fieldBegin' in s and 'type="HYPERLINK"' in s)
        check("fieldEnd 짝 존재", "<hp:fieldEnd" in s)
        check("URL 이스케이프 주입(&amp;)",
              "https://example.com/path?q=1&amp;x=2" in s)
        check("표시 문구 포함", "예시 링크" in s)
        check("하이퍼링크: 본문 보존", ANCHOR in s)
        ch, added = changed_entries(form, hl)
        check("하이퍼링크: header+section만 변경, 신규 엔트리 없음",
              ch == ["Contents/header.xml", "Contents/section0.xml"]
              and not added)
        check("하이퍼링크: 링크 run이 새 charPr 참조",
              rep and ('charPrIDRef="%s"' % rep["charPrId"]) in s)
        check("하이퍼링크: well-formed", wellformed(hl))
        code, _ = run("check", hl, "--strict")
        check("하이퍼링크: check --strict 통과", code == 0)

        # ── 책갈피 ──
        bm = d / "bm.hwpx"
        code, rep = run("add-bookmark", form, bm, "--after", ANCHOR,
                        "--name", "앵커1")
        check("add-bookmark 성공", code == 0 and rep and rep["ok"])
        s = sec(bm)
        check("hp:bookmark 마커 생성",
              '<hp:bookmark name="앵커1"/>' in s)
        check("책갈피: 본문 보존", ANCHOR in s)
        check("책갈피: section0만 변경",
              changed_entries(form, bm) == (["Contents/section0.xml"], []))
        check("책갈피: well-formed", wellformed(bm))
        code, _ = run("check", bm, "--strict")
        check("책갈피: check --strict 통과", code == 0)

        # ── 누락 인자 거부 ──
        code, _ = run("add-footnote", form, d / "x.hwpx", "--after", ANCHOR,
                      expect=2)
        check("각주: --text 누락 거부(argparse exit 2)", code == 2)
        code, _ = run("add-hyperlink", form, d / "x.hwpx", "--after", ANCHOR,
                      "--url", "  ", "--text", "t", expect=1)
        check("하이퍼링크: 빈 URL 거부(exit 1)", code == 1)
        code, _ = run("add-footnote", form, d / "x.hwpx",
                      "--after", "존재하지않는문구XYZ", "--text", "t", expect=1)
        check("각주: 없는 앵커 거부(exit 1)", code == 1)

        # ── 체이닝: 한 문서에 네 컨트롤 모두 적용 후 strict ──
        c1 = d / "c1.hwpx"
        c2 = d / "c2.hwpx"
        c3 = d / "c3.hwpx"
        c4 = d / "c4.hwpx"
        run("add-footnote", form, c1, "--after", ANCHOR, "--text", "각주")
        run("add-endnote", c1, c2, "--after", ANCHOR, "--text", "미주")
        run("add-hyperlink", c2, c3, "--after", ANCHOR,
            "--url", "https://a.b", "--text", "링크")
        code, _ = run("add-bookmark", c3, c4, "--after", ANCHOR, "--name", "bk")
        check("체이닝 4종 적용 성공", code == 0)
        s = sec(c4)
        check("체이닝: 4종 컨트롤 모두 존재",
              "<hp:footNote" in s and "<hp:endNote" in s
              and 'type="HYPERLINK"' in s and "<hp:bookmark" in s)
        check("체이닝: well-formed", wellformed(c4))
        code, _ = run("check", c4, "--strict")
        check("체이닝: check --strict 통과", code == 0)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
