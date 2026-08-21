#!/usr/bin/env python3
"""geomto.py(기본계획·검토보고 생성기) 테스트 — 형식의 세 층을 고정한다.

  기관 층: templates/geomto/header.xml 의 스타일 값
  내용 층: 장 번호 자동, 배너·제목 폭 = 본문 폭, 표지 결재 칸 = 인원수, 박스 높이 = 줄 수
  결정론: 같은 마크다운 → 같은 바이트
"""
import hashlib
import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import geomto  # noqa: E402
import yoyak  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


HDR = geomto.HEADER.read_text(encoding="utf-8")
charpr = lambda i: re.search(r'<hh:charPr id="%s".*?</hh:charPr>' % i, HDR, re.S).group(0)
parapr = lambda i: re.search(r'<hh:paraPr id="%s".*?</hh:paraPr>' % i, HDR, re.S).group(0)
borderfill = lambda i: re.search(r'<hh:borderFill id="%s".*?</hh:borderFill>' % i, HDR, re.S).group(0)

print("[기관 층 — header.xml]")
check("yoyak 스타일을 포함한 상위 집합(charPr 21·paraPr 41 존재)", '<hh:charPr id="21"' in HDR and '<hh:paraPr id="41"' in HDR)
check("제목 22pt HY헤드라인M 남색", 'height="2200"' in charpr(geomto.CP_TITLE) and 'hangul="4"' in charpr(geomto.CP_TITLE)
      and 'textColor="#203A7B"' in charpr(geomto.CP_TITLE))
check("장 번호 16pt 굵게 흰색", 'height="1600"' in charpr(geomto.CP_NUMERAL) and "<hh:bold/>" in charpr(geomto.CP_NUMERAL)
      and 'textColor="#FFFFFF"' in charpr(geomto.CP_NUMERAL))
check("장 제목 16pt HY헤드라인M", 'height="1600"' in charpr(geomto.CP_CHAP) and 'hangul="4"' in charpr(geomto.CP_CHAP))
check("▸ 3단·각주 14pt 휴먼명조", 'height="1400"' in charpr(geomto.CP_SUBSUB) and 'hangul="3"' in charpr(geomto.CP_SUBSUB))
check("표지 제목 28pt · 기관명 27pt · 부서 18pt", 'height="2800"' in charpr(geomto.CP_COVER_TITLE)
      and 'height="2700"' in charpr(geomto.CP_ORG_BIG) and 'height="1800"' in charpr(geomto.CP_ORG_SMALL))
check("제목 띠: 위 연파랑, 아래 남색 채움", 'faceColor="#C0CDEF"' in borderfill(geomto.BF_TITLE_TOP)
      and 'faceColor="#203A7B"' in borderfill(geomto.BF_TITLE_BOT))
check("장 번호 칸 파랑 채움 · 장 제목 칸 아래 0.7mm 파랑 선", 'faceColor="#3057B9"' in borderfill(geomto.BF_CHAP_NUM)
      and 'bottomBorder type="SOLID" width="0.7 mm" color="#3057B9"' in borderfill(geomto.BF_CHAP_TITLE))
check("표지 머리 칸 연회색 채움 + 실선", 'faceColor="#E6EEF7"' in borderfill(geomto.BF_COVER_LABEL)
      and borderfill(geomto.BF_COVER_LABEL).count('type="SOLID" width="0.12 mm"') == 4)
check("❍ 항목 내어쓰기 3644 · 앞 띄움 300", 'intent value="-3644"' in parapr(geomto.PP_ITEM) and 'prev value="300"' in parapr(geomto.PP_ITEM))
check("- 세부 내어쓰기 4174(실측)", 'intent value="-4174"' in parapr(geomto.PP_SUB))
check(f"본문 줄간격 기본 {geomto.LINE_SPACING}%", f'value="{geomto.LINE_SPACING}"' in parapr(geomto.PP_ITEM))
check("작성자 줄 오른쪽 정렬", 'horizontal="RIGHT"' in parapr(geomto.PP_AUTHOR))
check("전용 글꼴·그림 브러시 흔적 없음", not re.search(r"광양|감동체|햇살체|KoPub|imgBrush|gradation", HDR))

print("[파서]")
title, blocks = geomto.parse_body(
    "# T\n> 리드1\n> 리드2\n## 장1\n- 항목\n  - 세부\n    - 3단\n⇒ 결론\n※ 참고\n* 각주\n> 박스\n---\n## 장2\n| a | b |\n|---|---|\n| 1 | 2 |\n")
kinds = [b[0] for b in blocks]
check("블록 종류", kinds == ["box", "chapter", "item", "sub", "subsub", "concl", "note", "foot", "box", "pagebreak", "chapter", "table"], str(kinds))
check("인용 박스 여러 줄 묶음", blocks[0][1] == ["리드1", "리드2"])

print("[내용 층 — 계산]")
sec = geomto.build_section({"_base": Path(".")}, "T", blocks, [])
check("장 번호 Ⅰ Ⅱ 자동", "<hp:t>Ⅰ</hp:t>" in sec and "<hp:t>Ⅱ</hp:t>" in sec and "Ⅲ" not in sec)
check("장 배너 폭 = 본문 폭", f'<hp:sz width="{geomto.BODY_W}"' in geomto.chapter_banner(1, "x"))
check("제목 띠 폭 = 본문 폭", f'<hp:sz width="{geomto.BODY_W}"' in geomto.title_box("x"))
tb_sub = geomto.title_box("x", "부제")
tb_no = geomto.title_box("x")
h_sub = int(re.search(r'cellSz width="\d+" height="(\d+)"', tb_sub.split("</hp:tr>")[1]).group(1))
h_no = int(re.search(r'cellSz width="\d+" height="(\d+)"', tb_no.split("</hp:tr>")[1]).group(1))
check("부제가 있으면 제목 칸이 한 줄 높아진다", h_sub == h_no + geomto.SUBTITLE_ROW_H)
box1 = geomto.text_box(["한 줄"])
box3 = geomto.text_box(["한 줄", "두 줄", "세 줄"])
h1 = int(re.search(r'cellSz width="\d+" height="(\d+)"', box1).group(1))
h3 = int(re.search(r'cellSz width="\d+" height="(\d+)"', box3).group(1))
check("박스 높이는 줄 수를 따른다", h3 > h1)
check("리드(제목 바로 아래 인용)는 본문 첫 블록에서 빠진다", sec.count("리드1") == 1)
# 표지 결재 칸 = 인원수, 병합 덮임 정확
for n in (3, 5):
    t = geomto.cover_info_table({}, ["직위%d" % i for i in range(n)])
    m = re.search(r'rowCnt="(\d+)" colCnt="(\d+)"', t)
    r, c = int(m[1]), int(m[2])
    cov = [[0] * c for _ in range(r)]
    for tc in re.finditer(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"/><hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"/>', t):
        x, y, cs, rs = map(int, tc.groups())
        for yy in range(y, y + rs):
            for xx in range(x, x + cs):
                cov[yy][xx] += 1
    check(f"표지 표 결재자 {n}명 → 열 {c}개, 모든 칸이 정확히 한 번 덮임",
          c == 3 + n + 1 and all(v == 1 for row in cov for v in row))
check("표지 표 폭 ≤ 본문 폭", int(re.search(r'<hp:sz width="(\d+)"', geomto.cover_info_table({}, ["a"] * 5)).group(1)) <= geomto.BODY_W)
sec_cover = geomto.build_section({"표지": "true", "기관": "○○시", "결재": "주무관 / 팀장", "_base": Path(".")}, "T", [("chapter", "장")], [])
check("표지 켜면 쪽 나눔 1회 + 제목 띠 2회(표지·본문)", sec_cover.count('pageBreak="1"') == 1 and sec_cover.count(f'faceColor') == 0
      and sec_cover.count(f'borderFillIDRef="{geomto.BF_TITLE_BOT}"') == 2)
check("표지 없으면 쪽 나눔 없음 · 제목 띠 1회", sec.count('pageBreak="1"') == 1 and sec.count(f'borderFillIDRef="{geomto.BF_TITLE_BOT}"') == 1)
check("secPr 은 항상 1개", sec.count("<hp:secPr") == 1 and sec_cover.count("<hp:secPr") == 1)

print("[결정론 · 패키지]")
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / "s.md").write_text(geomto.SAMPLE, encoding="utf-8")
    geomto.generate(d / "s.md", d / "a.hwpx")
    geomto.generate(d / "s.md", d / "b.hwpx")
    check("같은 입력 → 같은 바이트", hashlib.md5((d / "a.hwpx").read_bytes()).hexdigest()
          == hashlib.md5((d / "b.hwpx").read_bytes()).hexdigest())
    z = zipfile.ZipFile(d / "a.hwpx")
    check("모든 엔트리 시각 1980-01-01", all(i.date_time[:3] == (1980, 1, 1) for i in z.infolist()))
    s = z.read("Contents/section0.xml").decode()
    h = z.read("Contents/header.xml").decode()
    used = lambda k: set(re.findall(k + r'IDRef="(\d+)"', s))
    dfn = lambda k: set(re.findall(r"<hh:" + k + r' id="(\d+)"', h))
    check("미정의 스타일 ID 없음", not (used("charPr") - dfn("charPr")) and not (used("paraPr") - dfn("paraPr"))
          and not (used("borderFill") - dfn("borderFill")))
    check("linesegarray 없음", "linesegarray" not in s)
    check("원본 문서 흔적 없음", not re.search(r"광양|김종호|강창성|감동시대|매돌이|종량제", s + h))
    check("날짜 메타 = 작성일", "2026-08-21T00:00:00Z" in z.read("Contents/content.hpf").decode())
    (d / "ls.md").write_text("---\n줄간격: 150\n---\n# 제목\n## 장\n- 항목\n", encoding="utf-8")
    geomto.generate(d / "ls.md", d / "ls.hwpx")
    hh = zipfile.ZipFile(d / "ls.hwpx").read("Contents/header.xml").decode()
    check("줄간격 150 → 본문 paraPr 에 반영, 원본 템플릿 불변",
          re.search(r'<hh:paraPr id="%s".*?value="150"' % geomto.PP_ITEM, hh, re.S) is not None
          and f'value="{geomto.LINE_SPACING}"' in parapr(geomto.PP_ITEM))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
