#!/usr/bin/env python3
"""fill_hwpx.py insert-chart 스모크 테스트 — 네이티브 차트 삽입(P10).

OOXML chartSpace 파트(Chart/chartN.xml) 생성 + content.hpf 매니페스트 등록 +
섹션에 인라인 <hp:chart> 참조 삽입. 본문 보존 + 한컴 열림 게이트(check
--strict) 확인.

사용법: python3 tests/test_chart.py
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
ASSETS = ROOT / "assets"

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


def zread(path, name):
    return zipfile.ZipFile(path).read(name).decode("utf-8")


def names(path):
    return zipfile.ZipFile(path).namelist()


def crc_map(path):
    return {i.filename: i.CRC for i in zipfile.ZipFile(path).infolist()}


def _well_formed(xml):
    try:
        minidom.parseString(xml)
        return True
    except Exception:  # noqa: BLE001
        return False


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)

        cat = d / "cat.json"
        series = d / "series.json"
        write_json(cat, ["1월", "2월", "3월", "4월"])
        write_json(series, [{"name": "매출 & 이익", "values": [120, 150, 90, 200]},
                            {"name": "비용", "values": [20, 35, 10, 55]}])

        # ── 1) 세로막대(col) 삽입 — 파트/매니페스트/참조 정합 ─────────
        out1 = d / "chart_col.hwpx"
        code, rep = run("insert-chart", form, out1, "--type", "col",
                        "--cat", cat, "--series", series)
        check("insert-chart col 성공", code == 0 and rep and rep["ok"])
        part = rep["chart_part"] if rep else ""
        check("차트 파트명 Chart/chart1.xml", part == "Chart/chart1.xml")
        check("차트 파트 zip에 추가됨", part in names(out1))
        check("매니페스트 등록", rep and rep["manifest_id"] == "chart1")
        hpf1 = zread(out1, "Contents/content.hpf")
        check("content.hpf에 opf:item 등록",
              f'href="{part}"' in hpf1 and 'id="chart1"' in hpf1)
        s1 = zread(out1, "Contents/section0.xml")
        check("섹션에 <hp:chart> 참조 삽입",
              f'chartIDRef="{part}"' in s1 and "<hp:chart" in s1)
        check("인라인 배치(treatAsChar=1)", 'treatAsChar="1"' in s1)
        cx1 = zread(out1, part)
        check("chartSpace 루트", "<c:chartSpace" in cx1)
        check("barChart + 세로(col) 방향",
              "<c:barChart>" in cx1 and 'val="col"' in cx1)
        check("계열 2개 직렬화", cx1.count("<c:ser>") == 2)
        check("범주 라벨 보존(numCache/strCache)",
              "1월" in cx1 and "2월" in cx1)
        check("값 보존", "<c:v>120</c:v>" in cx1 and "<c:v>200</c:v>" in cx1)
        check("계열명 XML 이스케이프", "매출 &amp; 이익" in cx1)
        check("차트 파트 well-formed", _well_formed(cx1))
        check("section0.xml well-formed", _well_formed(s1))
        check("content.hpf well-formed", _well_formed(hpf1))

        # ── 2) 원(pie) 차트 — 첫 계열만, 축 없음 ─────────────────────
        out2 = d / "chart_pie.hwpx"
        code, rep = run("insert-chart", form, out2, "--type", "pie",
                        "--cat", cat, "--series", series)
        check("insert-chart pie 성공", code == 0 and rep and rep["ok"])
        cx2 = zread(out2, "Chart/chart1.xml")
        check("pieChart 요소", "<c:pieChart>" in cx2)
        check("pie는 첫 계열만(1개)", cx2.count("<c:ser>") == 1)
        check("pie는 축 없음", "<c:catAx>" not in cx2 and "<c:valAx>" not in cx2)

        # ── 3) 나머지 타입 (bar/line/area) + check --strict ──────────
        for t, el in (("bar", "barChart"), ("line", "lineChart"),
                      ("area", "areaChart")):
            outt = d / f"chart_{t}.hwpx"
            code, rep = run("insert-chart", form, outt, "--type", t,
                            "--cat", cat, "--series", series)
            check(f"insert-chart {t} 성공", code == 0 and rep and rep["ok"])
            cxt = zread(outt, "Chart/chart1.xml")
            check(f"{t} → <c:{el}>", f"<c:{el}>" in cxt)
            code, crep = run("check", outt, "--strict")
            check(f"{t} check --strict 통과", code == 0 and crep and crep["ok"])

        # ── 4) 한컴 열림 게이트 (col/pie) ────────────────────────────
        for outp in (out1, out2):
            code, crep = run("check", outp, "--strict")
            check(f"{outp.name} check --strict 통과 (exit 0)",
                  code == 0 and crep and crep["ok"])

        # ── 5) 원본 보존 — section0.xml + content.hpf만 변경, 차트만 추가 ─
        be, oe = crc_map(form), crc_map(out1)
        changed = sorted(k for k in be if be[k] != oe.get(k))
        added = sorted(k for k in oe if k not in be)
        check("원본 보존: section0.xml + content.hpf만 변경",
              changed == ["Contents/content.hpf", "Contents/section0.xml"])
        check("추가 엔트리는 차트 파트뿐", added == ["Chart/chart1.xml"])
        check("기존 엔트리 바이트 보존(나머지 CRC 불변)",
              all(be[k] == oe[k] for k in be
                  if k not in ("Contents/content.hpf",
                               "Contents/section0.xml")))

        # ── 6) 차트 2개 삽입 — 파트/매니페스트 고유성 ────────────────
        out3 = d / "chart_twice.hwpx"
        code, rep = run("insert-chart", out1, out3, "--type", "pie",
                        "--cat", cat, "--series", series)
        check("2번째 차트 삽입 성공", code == 0 and rep and rep["ok"])
        check("2번째 파트는 chart2", rep and rep["chart_part"] == "Chart/chart2.xml")
        cs = sorted(n for n in names(out3) if n.startswith("Chart/"))
        check("차트 파트 2개 공존", cs == ["Chart/chart1.xml", "Chart/chart2.xml"])
        hpf3 = zread(out3, "Contents/content.hpf")
        check("매니페스트에 차트 2개", hpf3.count('href="Chart/chart') == 2)
        check("2개 삽입 후 zip 무결성",
              zipfile.ZipFile(out3).testzip() is None)
        check("2개 삽입 후 check --strict",
              run("check", out3, "--strict")[0] == 0)

        # ── 7) 위치 지정 (--after / --para) ──────────────────────────
        out4 = d / "chart_after.hwpx"
        anchor = "입사 지원 신청서"  # form.hwpx 본문 문구
        code, rep = run("insert-chart", form, out4, "--type", "col",
                        "--cat", cat, "--series", series, "--after", anchor)
        check("--after 삽입 성공", code == 0 and rep and rep["ok"])
        check("--after 후 check --strict",
              run("check", out4, "--strict")[0] == 0)

        out5 = d / "chart_para.hwpx"
        code, rep = run("insert-chart", form, out5, "--type", "col",
                        "--cat", cat, "--series", series, "--para", "0")
        check("--para 0 삽입 성공", code == 0 and rep and rep["ok"])

        # ── 8) 실제 한컴 저장본(asset) 대상 ──────────────────────────
        asset = ASSETS / "problem-answer-reference.hwpx"
        if asset.exists():
            out6 = d / "chart_asset.hwpx"
            code, rep = run("insert-chart", asset, out6, "--type", "col",
                            "--cat", cat, "--series", series)
            check("실제 한컴 저장본에 차트 삽입", code == 0 and rep and rep["ok"])
            check("asset 차트 파트 well-formed",
                  _well_formed(zread(out6, rep["chart_part"])))
            check("asset check --strict 통과",
                  run("check", out6, "--strict")[0] == 0)
            check("asset zip 무결성",
                  zipfile.ZipFile(out6).testzip() is None)

        # ── 9) 에러 처리 ─────────────────────────────────────────────
        bad = d / "chart_bad.hwpx"
        code, _ = run("insert-chart", form, bad, "--type", "col",
                      "--cat", cat, "--series", series,
                      "--after", "존재하지않는문구XYZ", expect=1)
        check("기준 문구 없음 → 실패(exit 1)", code == 1)
        check("실패 시 출력 미생성", not bad.exists())

        empty_cat = d / "empty_cat.json"
        write_json(empty_cat, [])
        code, _ = run("insert-chart", form, bad, "--type", "pie",
                      "--cat", empty_cat, "--series", series, expect=1)
        check("빈 범주 → 실패", code == 1)

        empty_ser = d / "empty_ser.json"
        write_json(empty_ser, [])
        code, _ = run("insert-chart", form, bad, "--type", "pie",
                      "--cat", cat, "--series", empty_ser, expect=1)
        check("빈 계열 → 실패", code == 1)

        code, _ = run("insert-chart", form, bad, "--type", "donut",
                      "--cat", cat, "--series", series, expect=2)
        check("미지원 타입 → argparse 거부(exit 2)", code == 2)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
