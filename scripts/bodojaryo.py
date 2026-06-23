#!/usr/bin/env python3
"""정부 표준 보도자료 HWPX 생성기 — 실제 레퍼런스 복제 방식(워크플로우 F).

assets/bodojaryo-reference.hwpx(국토교통부 실제 보도자료)를 **복제**하여
표·이미지(로고)·글꼴·스타일을 100% 보존하고, (1)본문 영역만 JSON으로 재생성,
(2)머리표/제목/부제/담당자 필드를 교체한다. 표 5개·이미지 6개를 새로 그리지 않으므로
한컴 호환성이 보장된다(SKILL.md: 표·이미지 多 → 처음부터 생성 금지, 복제 우선).

레퍼런스 양식 구조:
  표0  보도자료 + 상단 슬로건 로고
  표1  보도시점 : … / 배포 : …            (10pt 돋움 굵게)
  표2  제목(25pt 굵게) + 부제(- … -, 파랑)
  본문 □(14pt 바탕) → ㅇ(하위) → *(각주 12pt)   ← 이 영역만 재생성
  표3  담당 부서/책임자/담당자 + 연락처
  표4  하단 로고들

사용법:
    python3 scripts/bodojaryo.py --sample --output 보도자료.hwpx
    python3 scripts/bodojaryo.py --input bodo.json --output 보도자료.hwpx

JSON 스키마: --sample 참고. body=[[level,text],...] level 0=□ 1=ㅇ 2=*(각주).
로고를 본인 기관 것으로 바꾸려면 생성 후 한컴에서 이미지만 교체하면 된다.
"""
import argparse
import html
import json
import os
import re
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REF = SKILL_DIR / "assets" / "bodojaryo-reference.hwpx"


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# 본문 마커별 글자/문단 스타일 (레퍼런스에서 추출한 charPr/paraPr)
MARK = {0: "□", 1: "ㅇ", 2: "*"}


def _extract_body_templates(section):
    """본문 영역(표2~표3 사이)에서 □/ㅇ/* 문단을 양식 템플릿으로 추출."""
    tbls = list(re.finditer(r"<hp:tbl\b.*?</hp:tbl>", section, re.DOTALL))
    p2_end = section.find("</hp:p>", tbls[2].end()) + len("</hp:p>")
    p3_start = section.rfind("<hp:p ", 0, tbls[3].start())
    region = section[p2_end:p3_start]
    tpl = {}
    for pm in re.finditer(r"<hp:p\b[^>]*>.*?</hp:p>", region, re.DOTALL):
        block = pm.group(0)
        tm = re.search(r"<hp:t>(.*?)</hp:t>", block, re.DOTALL)
        if not tm:
            continue
        first = html.unescape(tm.group(1)).lstrip()[:1]
        for lv, mk in MARK.items():
            if first == mk and lv not in tpl:
                tpl[lv] = block
    # 누락 레벨은 □ 템플릿으로 대체
    base = tpl.get(0) or next(iter(tpl.values()))
    for lv in MARK:
        tpl.setdefault(lv, base)
    return tpl, p2_end, p3_start


_idc = [3000000000]


def _new_id():
    _idc[0] += 1
    return _idc[0]


def _fill_para(tpl, marker, text):
    """템플릿 문단의 스타일(paraPr·charPr)을 유지하고 텍스트만 교체, id 새로 부여."""
    p_open = re.match(r"<hp:p\b[^>]*?>", tpl).group(0)
    p_open = re.sub(r'\bid="\d+"', f'id="{_new_id()}"', p_open)
    cp = re.search(r'charPrIDRef="(\d+)"', tpl).group(1)
    body = f'<hp:run charPrIDRef="{cp}"><hp:t>{_esc(marker + " " + text)}</hp:t></hp:run>'
    return f"{p_open}{body}</hp:p>"


def build_section(meta, section):
    g = lambda k: meta.get(k, "") or ""
    tpl, p2_end, p3_start = _extract_body_templates(section)

    # 1) 본문 재생성
    paras = []
    for item in meta.get("body", []) or []:
        lv, text = item if isinstance(item, (list, tuple)) else (0, str(item))
        lv = int(lv)
        paras.append(_fill_para(tpl[lv], MARK[lv], text))
    new_body = "\n" + "\n".join(paras) + "\n"
    section = section[:p2_end] + new_body + section[p3_start:]

    # 2) 머리표/제목/부제/담당자 필드 교체 (원본 문자열 → JSON 값)
    repl = []
    repl.append(("보도시점 : 2026. 6. 23.(화) 11:00 이후(6. 24.(수) 조간) / 배포 : 2026. 6. 23.(화)",
                 g("보도시점라인") or f'보도시점 : {g("보도시점")} / 배포 : {g("배포")}'))
    repl.append(("24일 「2026년 철도의 날」 기념행사 개최", g("제목")))
    repl.append(("“세계를 여는 K-철도, 함께 성장하는 대한민국”", g("제목2")))
    부제 = meta.get("부제") or []
    repl.append(("우즈베키스탄·베트남 등 해외진출 성과 공유, 철도산업 발전 유공자 표창",
                 부제[0] if len(부제) > 0 else ""))
    repl.append(("철도산업 발전과 해외시장 진출 협력 세미나, 문학상 등 수상작",
                 부제[1] if len(부제) > 1 else ""))
    repl.append(("전시도 함께", ""))
    # 담당자 표
    담당 = meta.get("담당") or {}
    repl += [
        ("철도국", 담당.get("국", "○○국")),
        ("철도정책과", 담당.get("과", "○○과")),
        ("우정훈", 담당.get("책임자명", "")),
        ("(044-201-3938)", 담당.get("책임자전화", "")),
        ("서기관", 담당.get("담당자직급", "")),
        ("김재돈", 담당.get("담당자명", "")),
        ("(044-201-3939)", 담당.get("담당자전화", "")),
        ("주무관", 담당.get("주무관직급", "주무관")),
        ("박동준", 담당.get("주무관명", "")),
        ("(044-201-3943)", 담당.get("주무관전화", "")),
    ]
    for old, new in repl:
        if new is not None:
            section = section.replace(_esc(old), _esc(new), 1)
    return section


def generate(meta, output):
    with zipfile.ZipFile(REF) as z:
        section = z.read("Contents/section0.xml").decode("utf-8")
    new_section = build_section(meta, section)
    # PrvText
    texts = re.findall(r"<hp:t>(.*?)</hp:t>", new_section, re.DOTALL)
    prv = "\n".join(html.unescape(t) for t in texts if t.strip())
    with zipfile.ZipFile(REF) as zin, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "Contents/section0.xml":
                data = new_section.encode("utf-8")
            elif it.filename == "Preview/PrvText.txt":
                data = prv.encode("utf-8")
            if it.filename == "mimetype":
                zout.writestr(it, data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(it, data)
    return output


SAMPLE = {
    "보도시점": "2026. 6. 24.(수) 09:00 이후",
    "배포": "2026. 6. 23.(화)",
    "제목": "행안부, 공문서 작성 표준화 교육 전국 확대 시행",
    "제목2": "“정확한 공문서로 신뢰받는 행정”",
    "부제": ["2025 행정업무운영 편람 기반, 전 기관 문서 담당자 대상 권역별 교육",
             "작성법 자동 점검 도구 함께 배포, 기관별 자율 점검 추진"],
    "body": [
        [0, "행정안전부(장관 ○○○)는 2026. 7월부터 「2025 행정업무운영 편람」을 기반으로 한 "
            "공문서 작성 표준화 교육을 전국으로 확대 시행한다고 밝혔다."],
        [1, "이번 교육은 항목 기호 체계, 날짜·시간·금액 표기, 붙임·끝 표시 등 "
            "공문서 작성법의 정확성을 높이기 위한 것이다."],
        [2, "(대상) 중앙행정기관 및 지방자치단체 문서 담당자"],
        [0, "행정안전부는 교육 이후 기관별 자율 점검을 통해 작성법 준수율을 높여 나갈 계획이다."],
        [1, "문서 작성 오류를 자동으로 점검하는 도구도 함께 배포할 예정이다."],
    ],
    "담당": {"국": "디지털정부국", "과": "정보공개제도과",
            "책임자명": "홍길동", "책임자전화": "(044-205-2400)",
            "담당자직급": "사무관", "담당자명": "김철수", "담당자전화": "(044-205-2405)",
            "주무관명": "이영희", "주무관전화": "(044-205-2406)"},
}


def main():
    ap = argparse.ArgumentParser(description="정부 표준 보도자료 생성기(레퍼런스 복제 방식)")
    ap.add_argument("--input")
    ap.add_argument("--output", default="보도자료.hwpx")
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    meta = SAMPLE if args.sample else json.loads(Path(args.input).read_text(encoding="utf-8"))
    print("WROTE", generate(meta, args.output))


if __name__ == "__main__":
    main()
