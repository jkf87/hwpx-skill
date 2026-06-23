#!/usr/bin/env python3
"""행정안전부 표준 기안문(별지 제1호서식) HWPX 생성기.

「행정업무의 운영 및 혁신에 관한 규정 시행규칙」 별지 제1호서식과
2025 행정업무운영 편람(행정안전부, 2026. 1. 2.)을 기준으로,
두문(頭文)·본문·결문(結文)을 갖춘 표준 기안문을 생성한다.

  두문  : 행정기관명 → 수신 → (경유) → 제목
  본문  : 항목(1./가./1)/가)/(1)/(가)/①/㉮) + 붙임 + 끝
  결문  : 발신명의 → 기안자·검토자·결재권자 서명 → 협조자
          → 시행/접수(처리과명-일련번호) → 우편번호·주소·홈페이지
          → 전화·전송 → 전자우편 → 공개구분

서식 글꼴: 맑은 고딕 11.5pt(본문)·장평 100·자간 0·줄간격 103%,
발신명의 22pt·기관명 18pt·결문 9pt (gonmun2025 header.xml).

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
      "전화": "...", "전송": "...", "이메일": "...", "공개구분": "...",
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
CP_SENDER = "13"  # 22pt 굵게 — 발신명의
CP_LABEL = "14"   # 11.5pt 굵게 — 수신/제목 라벨
CP_FOOT = "15"    # 9pt — 결문
PP_BODY = "23"    # 양쪽혼합 103%
PP_CENTER = "24"  # 가운데 103%
PP_FOOT = "25"    # 왼쪽 103%

SEP = "─" * 46    # 결문 구분선 (텍스트 룰)

# 결문 9개 필드 + 두문 4개 + 본문
FIELDS = ["기관명", "수신", "경유", "제목", "발신명의", "기안자", "검토자",
          "결재권자", "협조자", "시행", "접수", "우편번호", "주소", "홈페이지",
          "전화", "전송", "이메일", "공개구분"]


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


def _empty():
    return _p(PP_BODY, [(CP_BODY, "")])


def _first_para(secpr, colpr):
    pid = next_id()
    return (f'<hp:p id="{pid}" paraPrIDRef="{PP_BODY}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{CP_BODY}">{secpr}{colpr}</hp:run></hp:p>')


def build_section(meta):
    """meta(dict) → 표준 기안문 section0.xml 문자열."""
    g = lambda k: meta.get(k, "") or ""
    secpr, colpr = _extract_secpr_colpr()
    reset_id(1000000000)
    P = ['<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>',
         f'<hs:sec {NS_DECL}>', _first_para(secpr, colpr)]

    # ── 두문 ──
    P.append(_p(PP_CENTER, [(CP_ORG, g("기관명"))]))
    P.append(_empty())
    P.append(_p(PP_BODY, [(CP_LABEL, "수신  "), (CP_BODY, g("수신"))]))
    경유 = g("경유")
    P.append(_p(PP_BODY, [(CP_BODY, "(경유)  " + 경유 if 경유 else "(경유)")]))
    P.append(_p(PP_BODY, [(CP_LABEL, "제목  "), (CP_BODY, g("제목"))]))
    P.append(_empty())

    # ── 본문 ──
    body = list(meta.get("body", []) or [])
    붙임 = meta.get("붙임") or []
    kkeut = meta.get("끝", True)
    # "끝" 표시: 붙임이 없으면 본문 마지막 글자에서 2타 띄우고 "끝." (편람 규칙)
    if kkeut and not 붙임 and body:
        body[-1] = body[-1] + "  끝."
    for line in body:
        P.append(_p(PP_BODY, [(CP_BODY, line)]))

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
    P.append(_p(PP_FOOT, [(CP_FOOT, SEP)]))
    sign = f"기안자 {g('기안자')}      검토자 {g('검토자')}      결재권자 {g('결재권자')}"
    P.append(_p(PP_FOOT, [(CP_FOOT, sign)]))
    if g("협조자"):
        P.append(_p(PP_FOOT, [(CP_FOOT, f"협조자 {g('협조자')}")]))
    P.append(_p(PP_FOOT, [(CP_FOOT, f"시행  {g('시행')}        접수  {g('접수')}")]))
    P.append(_p(PP_FOOT, [(CP_FOOT, f"우 {g('우편번호')}  {g('주소')}      /  {g('홈페이지')}")]))
    P.append(_p(PP_FOOT, [(CP_FOOT,
              f"전화 {g('전화')}      전송 {g('전송')}      /  {g('이메일')}      /  {g('공개구분')}")]))

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


def generate(meta, output):
    section_xml = build_section(meta)
    tmp_sec = Path(output).with_suffix(".section.tmp.xml")
    tmp_sec.write_text(section_xml, encoding="utf-8")
    subprocess.run([sys.executable, str(SKILL_DIR / "scripts/build_hwpx.py"),
                    "--header", str(GONMUN2025_HEADER), "--section", str(tmp_sec),
                    "--title", meta.get("제목", "기안문"), "--output", str(output)], check=True)
    subprocess.run([sys.executable, str(SKILL_DIR / "scripts/fix_namespaces.py"), str(output)], check=True)
    set_prvtext(output)
    tmp_sec.unlink(missing_ok=True)
    return output


SAMPLE = {
    "기관명": "행정안전부",
    "수신": "○○광역시장(자치행정과장)",
    "경유": "",
    "제목": "2026년 공문서 작성 표준화 교육 안내",
    "발신명의": "행정안전부장관",
    "기안자": "주무관 홍길동", "검토자": "정보공개제도과장 김영희", "결재권자": "행정제도국장 이철수",
    "협조자": "",
    "시행": "정보공개제도과-1234 (2026. 6. 22.)",
    "접수": "",
    "우편번호": "30112", "주소": "세종특별자치시 한누리대로 411(어진동)",
    "홈페이지": "www.mois.go.kr",
    "전화": "044-205-2345", "전송": "044-205-8910",
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
