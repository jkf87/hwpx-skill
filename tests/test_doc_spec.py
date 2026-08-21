#!/usr/bin/env python3
"""doc_spec.py — 편집 규범 추출·조판 회귀 테스트.

이 도구의 존재 이유를 고정한다: 내용 길이가 원본과 아무리 달라도
레이아웃이 어긋나지 않아야 한다(줄배치 캐시를 만들지 않으므로).
"""
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "scripts" / "doc_spec.py"
FILL = ROOT / "scripts" / "fill_hwpx.py"
VALIDATE = ROOT / "scripts" / "validate.py"
REF = ROOT / "assets" / "gyehoek-reference.hwpx"

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}")


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)],
                          capture_output=True, text=True)


def section_of(path):
    with zipfile.ZipFile(path) as z:
        return z.read("Contents/section0.xml").decode("utf-8")


def main():
    tmp = Path(tempfile.mkdtemp())
    spec_dir = tmp / "spec"

    # ── 추출 ──
    r = run(DS, "analyze", REF, "-o", spec_dir)
    check(r.returncode == 0, "analyze 성공")
    spec = json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))
    check(len(spec["levels"]) >= 5, f"본문 계층 추출({len(spec['levels'])}종)")
    check(any(k.startswith("banner") for k in spec["banners"]), "제목 배너 추출")
    check("callout" in spec["blocks"], "강조 박스 추출")
    check(any(k.startswith("table_") for k in spec["tables"]), "데이터 표 추출")
    check((spec_dir / "base.hwpx").is_file(), "스타일 공급용 base 보관")

    # ── 조판: 표가 내용 크기대로 만들어지는가 ──
    md = tmp / "c.md"
    rows = "\n".join(f"| 분야{i} | 과제 {i} | {i%4+1}분기 |" for i in range(1, 26))
    md.write_text(
        "# 제목\n## Ⅰ. 장\n### 1 절\n"
        "ㅇ 첫 항목\n- 하위 항목\n* 각주\n"
        "⇒ 결론 문장\n"
        "::: <상자 제목>\n￭ 가\n￭ 나\n￭ 다\n:::\n"
        "| 분야 | 과제명 | 일정 |\n| --- | --- | --- |\n" + rows + "\n",
        encoding="utf-8")
    out = tmp / "out.hwpx"
    r = run(DS, "render", spec_dir, md, "-o", out)
    check(r.returncode == 0, "render 성공")

    sec = section_of(out)
    check("<hp:linesegarray>" not in sec,
          "줄배치 캐시 0개 — 한컴이 재계산하므로 레이아웃이 안 어긋남")
    m = [x for x in re.finditer(r'rowCnt="(\d+)" colCnt="(\d+)"', sec)
         if int(x.group(1)) >= 4]
    check(bool(m) and m[0].group(1) == "26" and m[0].group(2) == "3",
          f"표가 내용 크기대로 26행x3열 (실제 {m[0].groups() if m else None})")
    check("과제 25" in sec, "표 마지막 행 내용이 실제로 들어감")
    check(":::" not in sec, "박스 닫는 표식이 본문에 새지 않음")

    # ── 게이트 ──
    check(run(VALIDATE, out, "--layout").returncode == 0, "validate --layout 통과")
    r = run(FILL, "check", out, "--strict")
    check(r.returncode == 0 and json.loads(r.stdout)["ok"], "check --strict 통과")

    # ── 극단 길이에서도 깨지지 않는가 ──
    tiny = tmp / "t.md"
    tiny.write_text("# 짧게\nㅇ 한 줄.\n", encoding="utf-8")
    out2 = tmp / "tiny.hwpx"
    check(run(DS, "render", spec_dir, tiny, "-o", out2).returncode == 0,
          "최소 내용도 조판됨")
    check(run(FILL, "check", out2, "--strict").returncode == 0,
          "최소 내용 결과가 strict 통과")

    big = tmp / "b.md"
    big.write_text("# 김\n## Ⅰ. 장\nㅇ " + ("아주 긴 문장입니다 " * 80) + "\n",
                   encoding="utf-8")
    out3 = tmp / "big.hwpx"
    check(run(DS, "render", spec_dir, big, "-o", out3).returncode == 0,
          "초장문도 조판됨")
    check(run(FILL, "check", out3, "--strict").returncode == 0,
          "초장문 결과가 strict 통과")

    # ── 원본 문구가 섞여 나오지 않는가 ──
    mp = ROOT / "scripts" / "map_preflight.py"
    r = run(mp, "residue", out, "--against", REF)
    check(r.returncode == 0, "산출물에 레퍼런스 원문 잔재 없음")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
