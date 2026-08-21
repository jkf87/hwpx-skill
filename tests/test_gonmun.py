#!/usr/bin/env python3
"""gonmun.py(표준 기안문 생성기) + gonmun_lint.py(작성법 검수기) 테스트.

사용법: python3 tests/test_gonmun.py
종료 코드 0이면 전체 통과. 풀 빌드(build_hwpx)는 lxml이 있으면 검증, 없으면 SKIP.
"""
import subprocess
import sys
import tempfile
import xml.dom.minidom as minidom
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gonmun          # noqa: E402
import gonmun_lint     # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


# ── 1. build_section: 구조·필드·글꼴 ──────────────────────────────
def wf(s):
    try:
        minidom.parseString(s); return True
    except Exception:
        return False


print("[build_section]")
section = gonmun.build_section(gonmun.SAMPLE)
check("XML well-formed", wf(section))
check("두문: 행정기관명 포함", "행정안전부" in section)
check("두문: 수신 라벨", "수신  " in section)
check("두문: 제목 라벨", "제목  " in section)
check("결문: 발신명의(행정안전부장관)", "행정안전부장관" in section)
check("결문: 결재란이 표(직위/성명 두 열)", "<hp:tbl" in section
      and 'colCnt="6"' in section and "주무관" in section and "홍길동" in section)
check("결재란 칸 너비 실측값(직위 52pt + 서명 65pt)",
      f'width="{gonmun.COL_TITLE}"' in section and f'width="{gonmun.COL_SIGN}"' in section)
check("결재란 표는 테두리 없음", f'borderFillIDRef="{gonmun.BF_NONE}"' in section)
check("결재일자는 8pt charPr", f'charPrIDRef="{gonmun.CP_DATE}"' in section
      and "2026. 6. 22." in section)
check("결문: 시행/접수", "시행  " in section and "접수  " in section)
check("결문: 우편번호·홈페이지", "우 30112" in section and "www.mois.go.kr" in section)
check("결문: 전화번호/팩스번호/이메일/공개", "전화번호 " in section
      and "팩스번호 " in section and "공개" in section)
# 라벨('붙임  ')과 값은 서로 다른 run이라 비연속 — 각각 확인. 1건이므로 '1.' 번호 없음
check("붙임: 1건 번호 생략 + 끝",
      "붙임  " in section and "교육 계획 1부.  끝." in section and "붙임  1. " not in section)
check("맑은고딕 본문 charPr(11) 사용", 'charPrIDRef="11"' in section)
check("발신명의 15pt charPr(실측값)", f'charPrIDRef="{gonmun.CP_SENDER}"' in section)
check("두문 아래 구분선", f'paraPrIDRef="{gonmun.PP_RULE_HEAD}"' in section)
check("결문 위 구분선(기본 회색)", f'paraPrIDRef="{gonmun.PP_RULE_GRAY}"' in section)
check("텍스트 룰('─' 반복)은 더 쓰지 않는다", "──────" not in section)
check("원훈(표어)이 맨 위", section.index("우리는 국민이") < section.index("행정안전부장관"))

# 결문선 변형 — 편람 표준 검정 실선
sec_black = gonmun.build_section({**gonmun.SAMPLE, "결문선": "검정"})
check("결문선 '검정' 지정 시 검정 실선 문단", f'paraPrIDRef="{gonmun.PP_RULE_BLACK}"' in sec_black
      and f'paraPrIDRef="{gonmun.PP_RULE_GRAY}"' not in sec_black)

# 예전 필드명(기안자/검토자/결재권자, 전화/전송)으로도 동작해야 한다
legacy = {k: v for k, v in gonmun.SAMPLE.items() if k not in ("결재", "전화번호", "팩스번호")}
legacy.update({"기안자": "주무관 홍길동", "검토자": "과장 김영희",
               "결재권자": "국장 이철수", "전화": "044-205-2345", "전송": "044-205-8910"})
sec_legacy = gonmun.build_section(legacy)
check("예전 기안자/검토자/결재권자 입력도 결재란으로",
      "<hp:tbl" in sec_legacy and "홍길동" in sec_legacy and "주무관" in sec_legacy)
check("예전 전화/전송 입력도 전화번호/팩스번호로",
      "전화번호 044-205-2345" in sec_legacy and "팩스번호 044-205-8910" in sec_legacy)

# 새 스타일 ID가 header.xml에 실제로 있어야 한다 — 없으면 한컴이 파일을 못 연다
_hdr = gonmun.GONMUN2025_HEADER.read_text(encoding="utf-8")
for _cp in (gonmun.CP_SENDER, gonmun.CP_SIGN, gonmun.CP_DATE):
    check(f"header.xml에 charPr {_cp} 정의", f'<hh:charPr id="{_cp}"' in _hdr)
for _pp in (gonmun.PP_RULE_HEAD, gonmun.PP_RULE_GRAY, gonmun.PP_RULE_BLACK):
    check(f"header.xml에 paraPr {_pp} 정의", f'<hh:paraPr id="{_pp}"' in _hdr)
check("구분선 borderFill이 아래 테두리만 SOLID",
      '<hh:borderFill id="5"' in _hdr and '<hh:borderFill id="6"' in _hdr)
check("secPr 포함(첫 문단)", "<hp:secPr" in section)
check("여백 좌우 20mm(5669)로 표준화", 'left="5669" right="5669"' in section)

# 끝 처리: 붙임 없을 때 본문 마지막에 "끝."
no_attach = dict(gonmun.SAMPLE); no_attach.pop("붙임")
sec2 = gonmun.build_section(no_attach)
# 본문에 '붙임·끝 표시' 문구가 있으므로 '붙임' 단어가 아니라 '붙임  ' 라벨 부재로 확인
check("붙임 없으면 본문 끝에 '끝.'", "  끝." in sec2 and "붙임  " not in sec2)

# ── 2. gonmun_lint: 위반 탐지 / 오탐 ──────────────────────────────
print("[gonmun_lint]")
bad = ("2025.1.6 회의를 오후 3시에 개최합니다.\n"
       "참가비 345천원\n"
       "기간: 2025. 2. 20.∼2. 24.까지\n"
       "업무협약 MOU(상호협력)\n"
       "'24. 1. 6. 기준 2025. 01. 06. 마감\n"
       "붙임: 계획서 1부.\n")
res = gonmun_lint.lint_text(bad)
rules = {f["rule"] for f in res["findings"]}
for r in ["DATE_NO_SPACE", "TIME_AMPM", "MONEY_CHEONWON", "KKAJI_DUP",
          "FOREIGN_FIRST", "DATE_2DIGIT_YR", "DATE_ZERO_PAD", "BUNIM_COLON"]:
    check(f"위반 탐지: {r}", r in rules)
check("error 존재 → summary.ok=False", res["summary"]["ok"] is False)

clean = ("1. 관련: 정보공개제도과-1000(2026. 6. 1.)\n"
         "  가. 일시: 2026. 7. 10.(금) 14:00∼17:00\n"
         "붙임  교육 계획 1부.  끝.\n")
res2 = gonmun_lint.lint_text(clean)
check("위반 탐지: DATE_KOREAN",
      any(f["rule"] == "DATE_KOREAN"
          for f in gonmun_lint.lint_text("일시: 2026년 8월 25일(화)")["findings"]))
# 결문 홈페이지 주소의 '://' 는 쌍점 규칙 대상이 아니다
_url = gonmun_lint.lint_text("우 41068  대구광역시 동구 첨단로, 53  /  http://www.nia.or.kr/")
check("URL '://' 오탐 없음",
      not any(f["rule"] == "COLON_SPACE" for f in _url["findings"]))
check("정상문 오탐 없음(error 0)", res2["summary"].get("error", 0) == 0,
      detail=str([f["rule"] for f in res2["findings"]]))

# ── 3. 풀 빌드(build_hwpx) — lxml 있으면 검증 ─────────────────────
print("[full build]")
try:
    import lxml  # noqa: F401
    have_lxml = True
except Exception:
    have_lxml = False

if not have_lxml:
    print("  ⊘ SKIP (lxml 미설치 — build_hwpx 검증 생략)")
else:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "g.hwpx"
        gonmun.generate(gonmun.SAMPLE, out)
        check("생성 파일 존재", out.exists())
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            check("section0.xml 존재", "Contents/section0.xml" in names)
            check("PrvText 본문 반영(raw 회피)",
                  "행정안전부장관" in z.read("Preview/PrvText.txt").decode("utf-8"))
        # check --strict
        r = subprocess.run([sys.executable, str(ROOT / "scripts/fill_hwpx.py"),
                            "check", str(out), "--strict"], capture_output=True, text=True)
        check("check --strict 통과(exit 0)", r.returncode == 0, detail=r.stderr[:200])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
