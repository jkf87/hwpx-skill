#!/usr/bin/env python3
"""행정안전부 표준 기안문(별지 제1호서식) HWPX 생성기.

「행정업무의 운영 및 혁신에 관한 규정 시행규칙」 별지 제1호서식과
2025 행정업무운영 편람(행정안전부, 2026. 1. 2.)을 기준으로,
두문(頭文)·본문·결문(結文)을 갖춘 표준 기안문을 생성한다.

  두문  : 원훈(표어) → 행정기관명 → 수신 → (경유) → 제목 → 구분선
  본문  : 항목(1./가./1)/가)/(1)/(가)/①/㉮) + 붙임 + 끝
  결문  : 발신명의 → 구분선 → 결재란(직위/서명/결재일자) → 협조자
          → 시행/접수(처리과명-일련번호) → 우편번호·주소·홈페이지
          → 전화번호·팩스번호 → 전자우편 → 공개구분

서식 글꼴: 맑은 고딕 11.5pt(본문)·장평 100·자간 0·줄간격 103%,
기관명 18pt·발신명의 15pt·결문 9pt (gonmun2025 header.xml).

두문·결문 구분선과 결재란 지오메트리는 실제 시행문(한국지능정보사회진흥원,
2026. 8. 21. 발신)을 PDF 좌표로 실측해 맞췄다:

  두문 구분선  제목 아래, 본문 폭 전체, 검정 0.4mm 실선
  결문 구분선  발신명의 아래, 본문 폭 전체, 회색(#CCCCCC) 3.0mm 굵은 선
  결재란       칸 간격 117pt = 직위칸 52pt + 서명칸 65pt, 서명칸 위에 결재일자 8pt

결문 구분선을 편람 표준의 검정 실선으로 바꾸려면 ``"결문선": "검정"``.

사용법:
    # JSON 입력으로 기안문 생성
    python3 scripts/gonmun.py --input gonmun.json --output 기안문.hwpx

    # 플레이스홀더 템플릿(section0.xml) 재생성
    python3 scripts/gonmun.py --emit-template

JSON 스키마(예시는 --sample 참고):
    {
      "기관명": "...", "수신": "...", "경유": "", "제목": "...",
      "발신명의": "...", "기안자": "...", "검토자": "...", "결재권자": "...",
      "협조자": "", "시행": "...", "접수": "...",
      "우편번호": "...", "주소": "...", "홈페이지": "...",
      "전화번호": "...", "팩스번호": "...", "이메일": "...", "공개구분": "...",
      "원훈": "우리는 ...",                    // 선택 — 상단 표어
      "결재": [{"직위": "...", "성명": "...", "일자": "2026. 8. 21."}],
      "결문선": "회색",                        // 회색(기본) | 검정
      "줄간격": 160,                           // 본문 줄간격(%) — 기본 160
      "항목간_빈줄": true,                     // 1./2./3. 사이 빈 줄(기본 true)
      "body": ["1. ...", "  가. ...", ...],   // 본문 항목(들여쓰기 포함)
      "붙임": ["계획서 1부.", ...],            // 선택
      "끝": true
    }
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from hwpx_helpers import next_id, reset_id, xml_escape, NS_DECL  # noqa: E402

GONMUN_TEMPLATE = SKILL_DIR / "templates" / "gonmun" / "section0.xml"
GONMUN2025_HEADER = SKILL_DIR / "templates" / "gonmun2025" / "header.xml"
GONMUN2025_SECTION = SKILL_DIR / "templates" / "gonmun2025" / "section0.xml"

# ── 스타일 ID (gonmun2025 header.xml) ──
CP_BODY = "11"    # 맑은 고딕 11.5pt 본문
CP_ORG = "12"     # 18pt 굵게 — 행정기관명
CP_LABEL = "14"   # 11.5pt 굵게 — 수신/제목 라벨
CP_FOOT = "15"    # 9pt — 결문·원훈
CP_SENDER = "16"  # 15pt 굵게 — 발신명의(실측값)
CP_SIGN = "17"    # 9pt 굵게 — 결재란 서명(성명)
CP_DATE = "18"    # 8pt — 결재일자
PP_BODY = "23"    # 양쪽혼합 103%
PP_CENTER = "24"  # 가운데 103%
PP_FOOT = "25"    # 왼쪽 103%
PP_RULE_HEAD = "26"   # 두문 구분선(검정 0.4mm 아래 테두리)
PP_RULE_GRAY = "27"   # 결문 구분선(회색 3.0mm)
PP_RULE_BLACK = "28"  # 결문 구분선(검정 0.4mm) — 편람 표준 변형

BF_NONE = "1"     # 테두리 없는 borderFill — 결재란 표

# 본문 줄간격(%). 편람 기본은 103%지만 실제 시행문은 12pt 글자에 19pt 줄 —
# 약 160%다. 103%로 짜면 본문이 눌려 붙어 공문으로 보이지 않는다.
LINE_SPACING = 160

# 최상위 항목(1. 2. 3. …) 사이에는 빈 줄이 들어간다
TOP_ITEM = re.compile(r"^\s*\d+\.\s")

# 결재란 지오메트리(HWPUNIT, 1pt=100) — 실제 시행문 PDF 좌표 실측
COL_TITLE = 5200   # 직위칸 52pt
COL_SIGN = 6500    # 서명칸 65pt  (합계 117pt = 실측 칸 간격)
ROW_DATE = 800     # 결재일자 행 8pt
ROW_SIGN = 1300    # 직위/서명 행 13pt

# 결문 9개 필드 + 두문 5개 + 본문
FIELDS = ["원훈", "기관명", "수신", "경유", "제목", "발신명의", "결재",
          "협조자", "시행", "접수", "우편번호", "주소", "홈페이지",
          "전화번호", "팩스번호", "이메일", "공개구분"]

# 예전 필드명 → 현행 필드명 (기존 JSON 입력을 그대로 받기 위함)
ALIASES = {"전화": "전화번호", "전송": "팩스번호"}


def _extract_secpr_colpr():
    """gonmun 템플릿에서 secPr+colPr(ctrl)를 추출하고 여백을 표준값으로 조정."""
    x = GONMUN_TEMPLATE.read_text(encoding="utf-8")
    secpr = re.search(r"<hp:secPr.*?</hp:secPr>", x, re.DOTALL).group(0)
    # 표준 여백: 좌우 20mm(5669), 위 20mm(5668), 아래 15mm(4252) — 규정 시행규칙 기준
    secpr = re.sub(r'(<hp:margin header="\d+" footer="\d+" gutter="\d+" )left="\d+" right="\d+"',
                   r'\g<1>left="5669" right="5669"', secpr)
    ctrl = re.search(r"<hp:ctrl>.*?</hp:ctrl>", x, re.DOTALL)
    colpr = ctrl.group(0) if ctrl else ""
    return secpr, colpr


def _p(parapr, runs):
    """문단: runs = [(charPrIDRef, text), ...]. text 빈 문자열이면 <hp:t/>."""
    pid = next_id()
    body = ""
    for cp, t in runs:
        if t == "":
            body += f'<hp:run charPrIDRef="{cp}"><hp:t/></hp:run>'
        else:
            body += f'<hp:run charPrIDRef="{cp}"><hp:t>{xml_escape(t)}</hp:t></hp:run>'
    return (f'<hp:p id="{pid}" paraPrIDRef="{parapr}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">{body}</hp:p>')


def _empty(parapr=PP_BODY):
    return _p(parapr, [(CP_BODY, "")])


def _rule(parapr):
    """가로 구분선 — 아래 테두리만 있는 빈 문단.

    텍스트 룰('─' 반복)은 글꼴에 따라 길이가 들쭉날쭉하고 본문 폭에 맞지도
    않는다. 문단 아래 테두리는 본문 폭 전체에 정확히 그려진다.
    """
    return _p(parapr, [(CP_FOOT, "")])


def _cell(col, row, width, height, paragraphs):
    """결재란 표의 칸. 테두리·여백 없음 — 글자가 칸 왼쪽 끝에 붙어야 한다."""
    # hasMargin="1" 이어야 아래 cellMargin(0)이 적용된다. 0으로 두면 한컴이
    # 표 기본 여백을 넣어 직위·성명 x좌표가 실측값에서 밀린다.
    return (f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" '
            f'dirty="0" borderFillIDRef="{BF_NONE}">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
            f'vertAlign="BOTTOM" linkListIDRef="0" linkListNextIDRef="0" '
            f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f'{"".join(paragraphs)}</hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
            f'<hp:cellSpan colSpan="1" rowSpan="1"/>'
            f'<hp:cellSz width="{width}" height="{height}"/>'
            f'<hp:cellMargin left="0" right="0" top="0" bottom="0"/></hp:tc>')


def _gyeoljae_table(결재):
    """결재란 — 칸마다 [직위][결재일자/성명] 두 열. 표로 짜야 열이 흔들리지 않는다.

    실측 시행문은 직위와 성명의 x좌표가 칸마다 정확히 같다. 탭으로 맞추면
    글자 폭에 따라 밀리므로 테두리 없는 표를 쓴다.
    """
    if not 결재:
        return ""
    widths = []
    for _ in 결재:
        widths += [COL_TITLE, COL_SIGN]
    rows = []
    for r, (height, pick) in enumerate(((ROW_DATE, "일자"), (ROW_SIGN, "성명"))):
        cells = []
        for i, item in enumerate(결재):
            title = item.get("직위", "") if pick == "성명" else ""
            value = item.get(pick, "") or ""
            cp = CP_SIGN if pick == "성명" else CP_DATE
            cells.append(_cell(i * 2, r, COL_TITLE, height,
                               [_p(PP_FOOT, [(CP_FOOT, title)])]))
            cells.append(_cell(i * 2 + 1, r, COL_SIGN, height,
                               [_p(PP_FOOT, [(cp, value)])]))
        rows.append(f'<hp:tr>{"".join(cells)}</hp:tr>')
    return (f'<hp:p id="{next_id()}" paraPrIDRef="{PP_FOOT}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{CP_FOOT}">'
            f'<hp:tbl id="{next_id()}" zOrder="0" numberingType="TABLE" '
            f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
            f'dropcapstyle="None" pageBreak="CELL" repeatHeader="0" '
            f'rowCnt="2" colCnt="{len(widths)}" cellSpacing="0" '
            f'borderFillIDRef="{BF_NONE}" noAdjust="0">'
            f'<hp:sz width="{sum(widths)}" widthRelTo="ABSOLUTE" '
            f'height="{ROW_DATE + ROW_SIGN}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" '
            f'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
            f'horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" '
            f'vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'{"".join(rows)}</hp:tbl></hp:run></hp:p>')


def _decisions(meta):
    """``결재`` 목록을 만든다. 예전 기안자/검토자/결재권자 입력도 그대로 받는다."""
    items = meta.get("결재")
    if items:
        return [dict(x) for x in items if (x.get("직위") or x.get("성명"))]
    out = []
    for key in ("기안자", "검토자", "결재권자"):
        v = (meta.get(key) or "").strip()
        if not v:
            continue
        # "주무관 홍길동" → 직위 '주무관', 성명 '홍길동'
        parts = v.rsplit(" ", 1)
        out.append({"직위": parts[0] if len(parts) == 2 else key,
                    "성명": parts[-1], "일자": ""})
    return out


def _first_para(secpr, colpr):
    pid = next_id()
    return (f'<hp:p id="{pid}" paraPrIDRef="{PP_BODY}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{CP_BODY}">{secpr}{colpr}</hp:run></hp:p>')


def build_section(meta):
    """meta(dict) → 표준 기안문 section0.xml 문자열."""
    meta = dict(meta)
    for old_key, new_key in ALIASES.items():          # 예전 필드명 흡수
        if old_key in meta and not meta.get(new_key):
            meta[new_key] = meta[old_key]
    g = lambda k: meta.get(k, "") or ""
    secpr, colpr = _extract_secpr_colpr()
    reset_id(1000000000)
    P = ['<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>',
         f'<hs:sec {NS_DECL}>', _first_para(secpr, colpr)]

    # ── 두문 ──
    if g("원훈"):
        P.append(_p(PP_CENTER, [(CP_FOOT, g("원훈"))]))
        P.append(_empty())
    P.append(_p(PP_CENTER, [(CP_ORG, g("기관명"))]))
    P.append(_empty())
    P.append(_empty())
    P.append(_p(PP_BODY, [(CP_LABEL, "수신  "), (CP_BODY, g("수신"))]))
    경유 = g("경유")
    P.append(_p(PP_BODY, [(CP_BODY, "(경유)  " + 경유 if 경유 else "(경유)")]))
    P.append(_p(PP_BODY, [(CP_LABEL, "제목  "), (CP_BODY, g("제목"))]))
    P.append(_rule(PP_RULE_HEAD))          # 두문과 본문을 가르는 실선
    P.append(_empty())

    # ── 본문 ──
    body = list(meta.get("body", []) or [])
    붙임 = meta.get("붙임") or []
    kkeut = meta.get("끝", True)
    # "끝" 표시: 붙임이 없으면 본문 마지막 글자에서 2타 띄우고 "끝." (편람 규칙)
    if kkeut and not 붙임 and body:
        body[-1] = body[-1] + "  끝."
    prev = ""
    for line in body:
        # 최상위 항목끼리 붙여 쓰면 문단 구분이 안 보인다. 이미 빈 줄을
        # 넣어 뒀으면 겹쳐 넣지 않는다.
        if TOP_ITEM.match(line) and prev.strip() and meta.get("항목간_빈줄", True):
            P.append(_empty())
        P.append(_p(PP_BODY, [(CP_BODY, line)]))
        prev = line

    # 붙임 — 쌍점(:) 없이 1자 여백, 1건이면 번호 생략, 끝은 붙임 끝에 2타 띄움
    if 붙임:
        P.append(_empty())
        if len(붙임) == 1:
            tail = "  끝." if kkeut else ""
            P.append(_p(PP_BODY, [(CP_LABEL, "붙임  "), (CP_BODY, 붙임[0] + tail)]))
        else:
            for i, b in enumerate(붙임):
                tail = "  끝." if (kkeut and i == len(붙임) - 1) else ""
                if i == 0:
                    P.append(_p(PP_BODY, [(CP_LABEL, "붙임  "), (CP_BODY, f"{i+1}. {b}{tail}")]))
                else:
                    P.append(_p(PP_BODY, [(CP_BODY, f"      {i+1}. {b}{tail}")]))

    # ── 결문 ──
    P.append(_empty())
    P.append(_p(PP_CENTER, [(CP_SENDER, g("발신명의"))]))
    P.append(_empty())
    P.append(_rule(PP_RULE_BLACK if g("결문선") == "검정" else PP_RULE_GRAY))
    결재 = _decisions(meta)
    if 결재:
        P.append(_gyeoljae_table(결재))
    P.append(_p(PP_FOOT, [(CP_FOOT, f"협조자 {g('협조자')}".rstrip())]))
    P.append(_p(PP_FOOT, [(CP_FOOT, f"시행  {g('시행')}        접수  {g('접수')}")]))
    P.append(_p(PP_FOOT, [(CP_FOOT, f"우 {g('우편번호')}  {g('주소')}      /  {g('홈페이지')}")]))
    P.append(_p(PP_FOOT, [(CP_FOOT,
              f"전화번호 {g('전화번호')}      팩스번호 {g('팩스번호')}"
              f"      /  {g('이메일')}      /  {g('공개구분')}")]))

    P.append("</hs:sec>")
    return "\n".join(P)


def set_prvtext(path):
    """Preview/PrvText.txt를 본문으로 채워 '한컴 미경유 raw(빈 페이지)' 플래그 회피."""
    with zipfile.ZipFile(path) as z:
        sec = z.read("Contents/section0.xml").decode("utf-8")
    texts = re.findall(r"<hp:t>(.*?)</hp:t>", sec, re.DOTALL)
    body = "\n".join(html.unescape(t) for t in texts if t.strip())
    tmp = str(path) + ".prv"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        wrote = False
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "Preview/PrvText.txt":
                data = body.encode("utf-8"); wrote = True
            if it.filename == "mimetype":
                zout.writestr(it, data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(it, data)
        if not wrote:
            zout.writestr("Preview/PrvText.txt", body.encode("utf-8"))
    os.replace(tmp, str(path))


def _header_for(meta, output):
    """본문 줄간격이 기본값과 다르면 header.xml 사본을 만들어 쓴다."""
    spacing = int(meta.get("줄간격") or LINE_SPACING)
    if spacing == LINE_SPACING:
        return GONMUN2025_HEADER, None
    x = GONMUN2025_HEADER.read_text(encoding="utf-8")
    m = re.search(r'<hh:paraPr id="%s".*?</hh:paraPr>' % PP_BODY, x, re.DOTALL)
    body_pr = m.group(0)
    fixed = body_pr.replace(f'<hh:lineSpacing type="PERCENT" value="{LINE_SPACING}"',
                            f'<hh:lineSpacing type="PERCENT" value="{spacing}"')
    if fixed == body_pr:
        raise SystemExit(f"줄간격을 바꿀 자리를 찾지 못했다(paraPr {PP_BODY})")
    tmp = Path(output).with_suffix(".header.tmp.xml")
    tmp.write_text(x[:m.start()] + fixed + x[m.end():], encoding="utf-8")
    return tmp, tmp


def generate(meta, output):
    section_xml = build_section(meta)
    tmp_sec = Path(output).with_suffix(".section.tmp.xml")
    tmp_sec.write_text(section_xml, encoding="utf-8")
    header, tmp_hdr = _header_for(meta, output)
    subprocess.run([sys.executable, str(SKILL_DIR / "scripts/build_hwpx.py"),
                    "--header", str(header), "--section", str(tmp_sec),
                    "--title", meta.get("제목", "기안문"), "--output", str(output)], check=True)
    subprocess.run([sys.executable, str(SKILL_DIR / "scripts/fix_namespaces.py"), str(output)], check=True)
    set_prvtext(output)
    tmp_sec.unlink(missing_ok=True)
    if tmp_hdr:
        tmp_hdr.unlink(missing_ok=True)
    return output


SAMPLE = {
    "원훈": "“우리는 국민이 주인인 나라, 함께 잘사는 대한민국을 만든다.”",
    "기관명": "행정안전부",
    "수신": "○○광역시장(자치행정과장)",
    "경유": "",
    "제목": "2026년 공문서 작성 표준화 교육 안내",
    "발신명의": "행정안전부장관",
    "결재": [
        {"직위": "주무관", "성명": "홍길동", "일자": ""},
        {"직위": "과장", "성명": "김영희", "일자": ""},
        {"직위": "국장", "성명": "이철수", "일자": "2026. 6. 22."},
    ],
    "협조자": "",
    "시행": "정보공개제도과-1234 (2026. 6. 22.)",
    "접수": "",
    "우편번호": "30112", "주소": "세종특별자치시 한누리대로 411(어진동)",
    "홈페이지": "www.mois.go.kr",
    "전화번호": "044-205-2345", "팩스번호": "044-205-8910",
    "이메일": "gildong@korea.kr", "공개구분": "공개",
    "body": [
        "1. 관련: 정보공개제도과-1000(2026. 6. 1.)「2026년 행정업무 혁신 추진계획」",
        "2. 「행정업무의 운영 및 혁신에 관한 규정」 및 2025 행정업무운영 편람에 따른 "
        "공문서 작성 표준화를 위하여 아래와 같이 교육을 안내하오니 많은 참여를 바랍니다.",
        "  가. 일시: 2026. 7. 10.(금) 14:00∼17:00",
        "  나. 장소: 정부세종청사 중앙동 대회의실",
        "  다. 대상: 각 기관 문서 담당자 100명",
        "  라. 내용: 항목 기호 체계, 날짜·시간·금액 표기, 붙임·끝 표시 실습",
    ],
    "붙임": ["2026년 공문서 작성 표준화 교육 계획 1부."],
    "끝": True,
}


def main():
    ap = argparse.ArgumentParser(description="행정안전부 표준 기안문(별지 제1호서식) 생성기")
    ap.add_argument("--input", help="기안문 JSON 입력 파일")
    ap.add_argument("--output", default="기안문.hwpx", help="출력 .hwpx 경로")
    ap.add_argument("--emit-template", action="store_true",
                    help="플레이스홀더 템플릿(templates/gonmun2025/section0.xml) 재생성")
    ap.add_argument("--sample", action="store_true", help="샘플 기안문 생성")
    args = ap.parse_args()

    if args.emit_template:
        placeholder = {f: "{{%s}}" % f for f in FIELDS}
        placeholder["body"] = ["1.  {{본문1}}", "  가.  {{본문2}}", "2.  {{본문3}}"]
        placeholder["붙임"] = ["{{붙임}} 1부."]
        placeholder["끝"] = True
        GONMUN2025_SECTION.write_text(build_section(placeholder), encoding="utf-8")
        print("WROTE", GONMUN2025_SECTION)
        return

    meta = SAMPLE if args.sample else json.loads(Path(args.input).read_text(encoding="utf-8"))
    out = generate(meta, args.output)
    print("WROTE", out)


if __name__ == "__main__":
    main()
