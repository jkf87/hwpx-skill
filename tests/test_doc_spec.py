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

    # ── 긴 셀 텍스트가 겹쳐 찍히지 않는가 (lineWrap SQUEEZE 사고) ──
    wmd = tmp / "w.md"
    wmd.write_text(
        "# 표\n## Ⅰ. 장\n"
        "| 분야 | 사업명 | 주요 내용 | 기대 효과 |\n| --- | --- | --- | --- |\n"
        "| AI | AI Hub | " + ("국내 최대 AI 학습 데이터 통합 플랫폼 고도화 및 민간 개방 확대 ")
        + "| 비용 절감 |\n", encoding="utf-8")
    outw = tmp / "wrap.hwpx"
    check(run(DS, "render", spec_dir, wmd, "-o", outw).returncode == 0,
          "긴 셀 텍스트 표 조판됨")
    secw = section_of(outw)
    check('lineWrap="SQUEEZE"' not in secw,
          "SQUEEZE 없음 — 있으면 자간을 줄여 글자가 겹쳐 찍힌다")
    check('lineWrap="BREAK"' in secw, "셀 줄바꿈이 BREAK 로 설정됨")
    check(run(FILL, "check", outw, "--strict").returncode == 0,
          "긴 셀 결과가 strict 통과")

    # ── 마크다운 굵게가 별표로 새지 않는가 (실사용에서 10곳 발생) ──
    emd = tmp / "e.md"
    emd.write_text("# 제목\n## Ⅰ. 장\n"
                   "ㅇ 본문에 **강조** 가 있다\n"
                   "| 구분 | 내용 |\n| --- | --- |\n"
                   "| **AI 인프라** | 설명 |\n| 데이터 | **중요** |\n",
                   encoding="utf-8")
    oute = tmp / "emph.hwpx"
    check(run(DS, "render", spec_dir, emd, "-o", oute).returncode == 0,
          "굵게 포함 원고 조판됨")
    sece = section_of(oute)
    texts = re.findall(r"<hp:t>(.*?)</hp:t>", sece, re.S)
    check(not any("**" in t for t in texts),
          "별표가 글자로 남지 않음 (한글은 마크다운 렌더러가 아님)")
    check("강조" in sece and "AI 인프라" in sece, "강조 텍스트 자체는 보존됨")
    # 굵게 charPr 이 실제로 쓰였는지 — 본문 charPr 과 다른 id 가 등장해야 한다
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(oute) as z:
        hdr = ET.fromstring(z.read("Contents/header.xml"))
    ns = {"hh": "http://www.hancom.co.kr/hwpml/2011/head"}
    bold_ids = {c.get("id") for c in hdr.findall(".//hh:charProperties/hh:charPr", ns)
                if c.find("hh:bold", ns) is not None}
    used = set(re.findall(r'charPrIDRef="(\d+)"', sece))
    check(bool(used & bold_ids), "굵게 charPr 이 실제로 적용됨(별표만 지운 게 아님)")
    check(run(FILL, "check", oute, "--strict").returncode == 0,
          "굵게 결과가 strict 통과")

    # ── 원본 문구가 섞여 나오지 않는가 ──
    mp = ROOT / "scripts" / "map_preflight.py"
    r = run(mp, "residue", out, "--against", REF)
    check(r.returncode == 0, "산출물에 레퍼런스 원문 잔재 없음")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
