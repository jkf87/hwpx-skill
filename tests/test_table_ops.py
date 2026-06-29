#!/usr/bin/env python3
"""fill_hwpx.py 표 구조/스타일 op 테스트 — set-cell/add-col/del-row/merge-cells.

각 op 후: 표 구조(rowCnt/colCnt/cellAddr) 정합, 원본보존(section/header만 변경),
한컴 열림 게이트(check --strict exit 0), XML well-formed(xml.etree)를 검증한다.

사용법: python3 tests/test_table_ops.py
종료 코드 0이면 전체 통과.
"""
import io
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
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
        print(f"    [exit {r.returncode} expect {expect}] {r.stderr.strip()[:200]}")
    out = None
    if r.stdout.strip():
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return r.returncode, out


def section_xml(path, name="Contents/section0.xml"):
    with zipfile.ZipFile(path) as zf:
        return zf.read(name).decode("utf-8")


def header_xml(path):
    with zipfile.ZipFile(path) as zf:
        n = next(x for x in zf.namelist() if x.endswith("header.xml"))
        return zf.read(n).decode("utf-8")


def table_block(xml, tbl_id):
    return re.search(r'<hp:tbl id="%s".*?</hp:tbl>' % tbl_id, xml, re.S).group(0)


def rows_of(tbl):
    return re.findall(r"<hp:tr>.*?</hp:tr>", tbl, re.S)


def cells_of(tr):
    return re.findall(r"<hp:tc\b.*?</hp:tc>", tr, re.S)


def xml_wellformed(path):
    try:
        with zipfile.ZipFile(path) as zf:
            for n in zf.namelist():
                if n.endswith(".xml"):
                    ET.fromstring(zf.read(n))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"    well-formed 실패: {e}")
        return False


def changed_entries(orig, out):
    """orig 대비 out에서 바이트가 달라진 ZIP 엔트리 목록 (목록/순서 변화 포함)."""
    def entries(p):
        with zipfile.ZipFile(p) as zf:
            return {i.filename: zf.read(i.filename) for i in zf.infolist()}
    a, b = entries(orig), entries(out)
    diff = [n for n in a if n in b and a[n] != b[n]]
    diff += [n for n in b if n not in a]  # 새 엔트리 (있으면 안 됨)
    return diff


def strict_ok(path):
    code, _ = run("check", path, "--strict")
    return code == 0


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tableops_"))
    form = tmp / "form.hwpx"
    subprocess.run([sys.executable, str(BUILD), str(form)], check=True,
                   capture_output=True)
    gyehoek = ASSETS / "gyehoek-reference.hwpx"

    # ── add-col: 표0 (id 9100, 5행×2열) 끝에 열 추가 ──────────────────
    print("[add-col]")
    out = tmp / "ac.hwpx"
    cells = tmp / "cells.json"
    cells.write_text(json.dumps(["a", "b", "c", "d", "e"]), encoding="utf-8")
    code, rep = run("add-col", form, out, "--table", 0, "--cells", cells)
    check("add-col exit 0", code == 0)
    tbl = table_block(section_xml(out), "9100")
    check("colCnt 2→3", re.search(r'colCnt="(\d+)"', tbl).group(1) == "3")
    rows = rows_of(tbl)
    check("모든 행 셀 3개", all(len(cells_of(r)) == 3 for r in rows),
          str([len(cells_of(r)) for r in rows]))
    last_addr = [re.findall(r'colAddr="(\d+)"', r)[-1] for r in rows]
    check("새 셀 colAddr=2", all(a == "2" for a in last_addr), str(last_addr))
    newtexts = ["".join(re.findall(r"<hp:t>([^<]*)</hp:t>", cells_of(r)[-1]))
                for r in rows]
    check("새 셀 값 채워짐", newtexts == ["a", "b", "c", "d", "e"], str(newtexts))
    check("원본보존(section만 변경)",
          changed_entries(form, out) == ["Contents/section0.xml"],
          str(changed_entries(form, out)))
    check("XML well-formed", xml_wellformed(out))
    check("check --strict exit 0", strict_ok(out))

    # ── add-col --at 0: 표1 (id 9200, 2×2) 앞에 열 삽입 ───────────────
    print("[add-col --at]")
    out_at = tmp / "at0.hwpx"
    code, _ = run("add-col", form, out_at, "--table", 1, "--at", 0)
    check("add-col --at exit 0", code == 0)
    tbl = table_block(section_xml(out_at), "9200")
    check("colCnt 2→3", re.search(r'colCnt="(\d+)"', tbl).group(1) == "3")
    addrs = re.findall(r'colAddr="(\d+)"', rows_of(tbl)[0])
    check("colAddr 재정합 0,1,2", addrs == ["0", "1", "2"], str(addrs))
    check("check --strict exit 0", strict_ok(out_at))

    # ── del-row: 표0 (5행) 1행 삭제 ──────────────────────────────────
    print("[del-row]")
    out = tmp / "dr.hwpx"
    code, _ = run("del-row", form, out, "--table", 0, "--row", 1)
    check("del-row exit 0", code == 0)
    tbl = table_block(section_xml(out), "9100")
    check("rowCnt 5→4", re.search(r'rowCnt="(\d+)"', tbl).group(1) == "4")
    rows = rows_of(tbl)
    check("행 4개", len(rows) == 4)
    raddrs = [re.findall(r'rowAddr="(\d+)"', r)[0] for r in rows]
    check("rowAddr 0,1,2,3 연속", raddrs == ["0", "1", "2", "3"], str(raddrs))
    check("삭제된 행 텍스트(연락처) 제거", "연락처" not in tbl)
    check("원본보존(section만 변경)",
          changed_entries(form, out) == ["Contents/section0.xml"])
    check("XML well-formed", xml_wellformed(out))
    check("check --strict exit 0", strict_ok(out))

    # ── merge-cells: 표1 (2×2) 전체 병합 ─────────────────────────────
    print("[merge-cells]")
    out = tmp / "mc.hwpx"
    code, rep = run("merge-cells", form, out, "--table", 1,
                    "--row", 0, "--col", 0, "--row2", 1, "--col2", 1)
    check("merge exit 0", code == 0)
    check("removed_cells=3", rep and rep.get("removed_cells") == 3)
    tbl = table_block(section_xml(out), "9200")
    check("셀 1개만 남음", len(re.findall(r"<hp:tc\b", tbl)) == 1)
    sp = re.search(r'<hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"', tbl)
    check("앵커 colSpan/rowSpan=2,2", sp and sp.groups() == ("2", "2"),
          str(sp.groups() if sp else None))
    check("rowCnt/colCnt 불변(2/2)",
          re.search(r'rowCnt="(\d+)"', tbl).group(1) == "2"
          and re.search(r'colCnt="(\d+)"', tbl).group(1) == "2")
    check("원본보존(section만 변경)",
          changed_entries(form, out) == ["Contents/section0.xml"])
    check("XML well-formed", xml_wellformed(out))
    check("check --strict exit 0", strict_ok(out))

    # 병합 표에 구조 op은 거부(exit 1)
    code, _ = run("add-col", out, tmp / "x.hwpx", "--table", 1, expect=1)
    check("병합 표 add-col 거부 exit 1", code == 1)
    code, _ = run("del-row", out, tmp / "x.hwpx", "--table", 1, "--row", 0,
                  expect=1)
    check("병합 표 del-row 거부 exit 1", code == 1)

    # ── set-cell: 실제 한컴 저장본(gyehoek) 셀 배경+테두리 ────────────
    print("[set-cell]")
    out = tmp / "sc.hwpx"
    code, rep = run("set-cell", gyehoek, out, "--table", 0,
                    "--row", 0, "--col", 0, "--bg", "FFE600", "--border", "on")
    check("set-cell exit 0", code == 0)
    h = header_xml(out)
    new_id = rep["borderFillIDRef"]
    bf = re.search(r'<hh:borderFill id="%s".*?</hh:borderFill>' % new_id,
                   h, re.S).group(0)
    check("새 borderFill faceColor=#FFE600",
          re.search(r'faceColor="([^"]+)"', bf).group(1) == "#FFE600")
    check("새 borderFill 4면 SOLID",
          len(re.findall(r'Border type="SOLID"', bf)) == 4)
    # itemCnt가 borderFill 개수와 일치하는지 (보정 정합)
    m = re.search(r'<hh:borderFills itemCnt="(\d+)"', h)
    n_bf = len(re.findall(r"<hh:borderFill\b", h))
    check("borderFills itemCnt == 실제 개수",
          m and int(m.group(1)) == n_bf, f"{m.group(1) if m else '?'} vs {n_bf}")
    # 대상 셀의 borderFillIDRef가 새 id로 바뀜
    tc0 = section_xml(out, _section_name(out))
    first_tc = re.search(r"<hp:tc\b.*?</hp:tc>", tc0, re.S).group(0)
    check("셀 borderFillIDRef 갱신",
          re.search(r'borderFillIDRef="(\d+)"', first_tc).group(1) == new_id)
    changed = changed_entries(gyehoek, out)
    check("원본보존(section+header만 변경)",
          set(changed) <= {_section_name(out), "Contents/header.xml"}
          and "Contents/header.xml" in changed, str(changed))
    check("XML well-formed", xml_wellformed(out))
    check("check --strict exit 0", strict_ok(out))

    # 같은 배경 재적용 → borderFill 재사용(헤더 변동 없음, 멱등)
    out2 = tmp / "sc2.hwpx"
    run("set-cell", out, out2, "--table", 0, "--row", 0, "--col", 0,
        "--bg", "FFE600", "--border", "on")
    check("동일 스타일 재적용 시 borderFill 재사용",
          changed_entries(out, out2) == [], str(changed_entries(out, out2)))

    # set-cell은 병합 표에도 허용(구조 미변경)
    code, _ = run("set-cell", tmp / "mc.hwpx", tmp / "scspan.hwpx",
                  "--table", 1, "--row", 0, "--col", 0, "--bg", "AABBCC")
    check("병합 표에도 set-cell 허용", code == 0)
    check("병합+set-cell check --strict", strict_ok(tmp / "scspan.hwpx"))

    # 잘못된 색 형식 거부
    code, _ = run("set-cell", gyehoek, tmp / "x.hwpx", "--table", 0,
                  "--row", 0, "--col", 0, "--bg", "ZZZ", expect=1)
    check("잘못된 색 거부 exit 1", code == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def _section_name(path):
    with zipfile.ZipFile(path) as zf:
        return sorted(n for n in zf.namelist()
                      if re.search(r"section\d+\.xml$", n, re.I))[0]


if __name__ == "__main__":
    sys.exit(main())
