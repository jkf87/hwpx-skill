#!/usr/bin/env python3
"""yoyak.py(요약보고 생성기) 테스트 — 형식의 세 층을 각각 고정한다.

  기관 층: templates/yoyak/header.xml 의 스타일 값 (글꼴·크기·정렬·줄간격·테두리)
  내용 층: 결재자 수 → 칸 높이, 글자 수 → 표 칸 너비, 그림 → BinData 등록
  결정론: 같은 마크다운 → 같은 바이트
"""
import hashlib
import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
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


HDR = yoyak.HEADER.read_text(encoding="utf-8")


def charpr(i):
    return re.search(r'<hh:charPr id="%s".*?</hh:charPr>' % i, HDR, re.S).group(0)


def parapr(i):
    return re.search(r'<hh:paraPr id="%s".*?</hh:paraPr>' % i, HDR, re.S).group(0)


def borderfill(i):
    return re.search(r'<hh:borderFill id="%s".*?</hh:borderFill>' % i, HDR, re.S).group(0)


fonts = dict(re.findall(r'<hh:font id="(\d+)" face="([^"]+)"', HDR))

print("[기관 층 — header.xml]")
check("글꼴 7벌 (한글 목록)", fonts.get("3") == "휴먼명조" and fonts.get("4") == "HY헤드라인M"
      and fonts.get("5") == "HY울릉도M" and fonts.get("6") == "한양신명조")
check("전용 글꼴 흔적 없음", not re.search(r"광양|감동체|햇살체|KoPub", HDR))
check("제목 20pt HY헤드라인M", 'height="2000"' in charpr(yoyak.CP_TITLE) and 'hangul="4"' in charpr(yoyak.CP_TITLE))
check("소제목 16pt HY헤드라인M", 'height="1600"' in charpr(yoyak.CP_H1))
check("본문 15pt 휴먼명조 장평 98", 'height="1500"' in charpr(yoyak.CP_BODY) and 'hangul="3"' in charpr(yoyak.CP_BODY)
      and 'ratio hangul="98"' in charpr(yoyak.CP_BODY))
check("본문 강조 4종(굵게/밑줄/파랑/형광)",
      "<hh:bold/>" in charpr(yoyak.CP_BODY_B) and 'type="BOTTOM"' in charpr(yoyak.CP_BODY_U)
      and 'textColor="#0000FF"' in charpr(yoyak.CP_BODY_BLUE) and 'shadeColor="#FFFF00"' in charpr(yoyak.CP_BODY_HL))
check("결재선 11pt HY울릉도M", 'height="1100"' in charpr(yoyak.CP_APPR) and 'hangul="5"' in charpr(yoyak.CP_APPR))
check("표 13pt 한양신명조, 머리 굵게", 'height="1300"' in charpr(yoyak.CP_TBL) and 'hangul="6"' in charpr(yoyak.CP_TBL)
      and "<hh:bold/>" in charpr(yoyak.CP_TBL_B))
check("□ 소제목 내어쓰기 9622", 'intent value="-9622"' in parapr(yoyak.PP_H1))
check("❍ 항목 내어쓰기 3644 · 양쪽정렬", 'intent value="-3644"' in parapr(yoyak.PP_ITEM) and 'horizontal="JUSTIFY"' in parapr(yoyak.PP_ITEM))
check("- 세부 내어쓰기 4594", 'intent value="-4594"' in parapr(yoyak.PP_SUB))
check("머리 문단 양끝정렬(로고 ↔ 결재선)", 'horizontal="DISTRIBUTE"' in parapr(yoyak.PP_HEAD))
check("직위 칸 양끝정렬, 성명 칸 가운데", 'horizontal="DISTRIBUTE"' in parapr(yoyak.PP_POS) and 'horizontal="CENTER"' in parapr(yoyak.PP_NAME))
check(f"본문 줄간격 기본 {yoyak.LINE_SPACING}%", f'value="{yoyak.LINE_SPACING}"' in parapr(yoyak.PP_ITEM))
check("결재선 보고일 칸: 아래 점선", 'bottomBorder type="DASH"' in borderfill(yoyak.BF_APPR_TOP))
check("결재선 직위·성명 칸: 위 점선", 'topBorder type="DASH"' in borderfill(yoyak.BF_APPR_BOT))
check("제목 띠: 테두리 없이 색 채움", 'faceColor="#2F5597"' in borderfill(yoyak.BF_TITLE_BAR) and 'type="SOLID"' not in borderfill(yoyak.BF_TITLE_BAR).replace('diagonal type="SOLID"', ""))
check("표 머리행 음영", 'faceColor="#DFE6F7"' in borderfill(yoyak.BF_TBL_HEAD))
check("그림 브러시(원본 로고 띠) 없음", "imgBrush" not in HDR)

print("[마크다운 파서]")
meta, body = yoyak.parse_front_matter("---\n기관: A\n결재: 단장 홍길동 / 팀장 김철수(12)\n---\n# 제목\n본문")
check("front matter 파싱", meta["기관"] == "A" and body.startswith("# 제목"))
ap = yoyak.parse_approvers(meta["결재"])
check("결재자 2명, 마지막 낱말이 성명", ap == [{"직위": "단장", "성명": "홍길동"}, {"직위": "팀장", "성명": "김철수(12)"}])
title, blocks = yoyak.parse_body("# T\n리드\n## 소\n- 항목\n  - 세부\n⇒ 결론\n=> 결론2\n※ 참고\n| a | b |\n|---|---|\n| 1 | 2 |\n![x](p.png)\n")
kinds = [b[0] for b in blocks]
check("블록 종류 판별", kinds == ["para", "h1", "item", "sub", "concl", "concl", "note", "table", "image"], str(kinds))
check("표 구분선 제거·셀 분리", blocks[7][1] == [["a", "b"], ["1", "2"]])

print("[내용 층 — 계산]")
sec2 = yoyak.build_section({"결재": "단장 홍 / 팀장 김", "보고일": "2026. 1. 1."}, "T", [], [])
sec3 = yoyak.build_section({"결재": "담당 박 / 팀장 김 / 단장 홍", "보고일": "2026. 1. 1."}, "T", [], [])
h2 = re.search(r'cellSz width="%d" height="(\d+)"' % yoyak.APPR_COL_POS, sec2).group(1)
h3 = re.search(r'cellSz width="%d" height="(\d+)"' % yoyak.APPR_COL_POS, sec3).group(1)
check("결재자 2명 → 칸 높이 2줄", int(h2) == yoyak.APPR_LINE_H * 2)
check("결재자 3명 → 칸 높이 3줄", int(h3) == yoyak.APPR_LINE_H * 3)
check("제목 띠 폭 = 본문 폭", f'width="{yoyak.BODY_W}"' in yoyak.title_box("x"))
tbl_a = yoyak.content_table([["구분", "내용"], ["가", "아주 긴 내용이 들어가는 칸입니다 정말로"]])
tbl_b = yoyak.content_table([["구분", "내용"], ["아주 긴 내용이 들어가는 칸입니다 정말로", "가"]])
wa = [int(x) for x in re.findall(r'cellSz width="(\d+)"', tbl_a)[:2]]
wb = [int(x) for x in re.findall(r'cellSz width="(\d+)"', tbl_b)[:2]]
check("표 칸 너비는 글자 수를 따른다", wa[0] < wa[1] and wb[0] > wb[1])
check("표 칸 너비 합 = 본문 폭", sum(wa) == yoyak.BODY_W)
check("강조 표식이 런으로 쪼개진다", 'charPrIDRef="%s"' % yoyak.CP_BODY_BLUE in yoyak.runs("a ++b++ c", yoyak.CP_BODY)
      and "++" not in yoyak.runs("a ++b++ c", yoyak.CP_BODY))
check("글머리 접두 공백 유지(내어쓰기 짝)", yoyak.PREFIX["item"] == "  ❍ " and yoyak.PREFIX["sub"].startswith("     -"))

print("[결정론 · 패키지]")
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / "s.md").write_text(yoyak.SAMPLE, encoding="utf-8")
    yoyak.generate(d / "s.md", d / "a.hwpx")
    yoyak.generate(d / "s.md", d / "b.hwpx")
    ha = hashlib.md5((d / "a.hwpx").read_bytes()).hexdigest()
    hb = hashlib.md5((d / "b.hwpx").read_bytes()).hexdigest()
    check("같은 입력 → 같은 바이트", ha == hb)
    z = zipfile.ZipFile(d / "a.hwpx")
    check("모든 엔트리 시각 1980-01-01", all(i.date_time[:3] == (1980, 1, 1) for i in z.infolist()))
    check("mimetype 첫 엔트리·무압축", z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == 0)
    sec = z.read("Contents/section0.xml").decode()
    hpf = z.read("Contents/content.hpf").decode()
    check("secPr 1개", sec.count("<hp:secPr") == 1)
    check("linesegarray 없음", "linesegarray" not in sec)
    check("제목이 content.hpf 에", "<opf:title>「○○시 공공 AX 실습교육」 운영 현황 보고</opf:title>" in hpf)
    check("날짜 메타 = 보고일(현재 시각 아님)", "2026-08-21T00:00:00Z" in hpf)
    check("원본 문서 흔적 없음", not re.search(r"광양|김종호|강창성|감동시대", sec + hpf))
    check("PrvText 채움", len(z.read("Preview/PrvText.txt")) > 100)
    # 그림 + 로고 + 줄간격
    from PIL import Image
    Image.new("RGB", (200, 80), (255, 255, 255)).save(d / "logo.png")
    Image.new("RGB", (400, 300), (200, 200, 255)).save(d / "fig.png")
    (d / "i.md").write_text("---\n로고: logo.png\n보고일: 2026. 3. 2.(월)\n결재: 단장 홍\n줄간격: 150\n---\n# 제목\n## 소\n![f](fig.png)\n", encoding="utf-8")
    yoyak.generate(d / "i.md", d / "i.hwpx")
    zi = zipfile.ZipFile(d / "i.hwpx")
    names = zi.namelist()
    check("로고·그림이 BinData 에", "BinData/image1.png" in names and "BinData/image2.png" in names)
    check("content.hpf 에 그림 등록", zi.read("Contents/content.hpf").decode().count("isEmbeded") == 2)
    hi = zi.read("Contents/header.xml").decode()
    check("줄간격 150 이 본문 paraPr 에 반영", re.search(r'<hh:paraPr id="%s".*?value="150"' % yoyak.PP_ITEM, hi, re.S) is not None)
    check("원본 header.xml 은 그대로", f'value="{yoyak.LINE_SPACING}"' in parapr(yoyak.PP_ITEM))
    si = zi.read("Contents/section0.xml").decode()
    check("로고는 머리 문단 안 pic, 결재선 표와 같은 문단", si.count("<hp:pic") == 2 and re.search(r"<hp:pic.*?binaryItemIDRef=\"image1\".*?<hp:tbl", si, re.S) is not None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
