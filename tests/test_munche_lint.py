#!/usr/bin/env python3
"""munche_lint.py 테스트 — 실측 문체 규칙을 잡고, 정상 개조식에는 오탐이 없어야 한다."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import munche_lint as M  # noqa: E402
import geomto, yoyak  # noqa: E402

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name} {detail}")

def rules(md):
    return [f["rule"] for f in M.lint(md)["findings"]]

print("[위반 탐지]")
check("'~다' 서술형 종결", "DA_ENDING" in rules("- 학교 단위로 설계해야 현장에 남는다"))
check("'~해야 한다' 당위", "DEONTIC" in rules("⇒ 학교 단위로 설계해야 한다"))
check("수사적 대조(B 가 추상어)", "RHETORIC_CONTRAST" in rules("- 부족한 것은 도구가 아니라 사람"))
check("⇒ 줄의 대조는 수사로 본다", "RHETORIC_CONTRAST" in rules("⇒ 연수가 아니라 전환"))
check("구체 선택지 대조는 경고만", rules("- BIE 공인박람회가 아닌 정부승인 국제행사 개최") == ["CONTRAST_CHECK"])
check("물음표", "QUESTION_EXCLAIM" in rules("- 왜 안 되는가?"))
check("줄표(—) 설명", "EM_DASH" in rules("- 결과물은 공유 — 저장소에 축적"))
check("❍ 항목 과장", "ITEM_LONG" in rules("- " + "가" * 80))
check("리드문 종결", "LEAD_ENDING" in rules("# T\n> AI 를 수업에 쓰는 전환을 3년에 걸쳐 추진한다."))
check("리드문 '~고자 함.' 통과(여러 줄 묶음)", "LEAD_ENDING" not in rules("# T\n> 실습형 교육을 도입하고,\n> 공유 구조를 만들어 정착시키고자 함."))
check("한글 연월일", "DATE_KOREAN" in rules("- 2026년 8월 22일 개최"))

print("[오탐 없음]")
ok = "# T\n> 연수를 도입하고, 서식을 공유하여 활용을 정착시키고자 함.\n## 추진배경\n- **(환경 변화)** 생성형 AI 확산으로 업무 방식 변화\n  - 학교 현장의 활용은 개인 차원에 한정, 격차로 연결\n⇒ 과제형 실습 연수로 전환 필요\n※ 정량 목표는 1차년 결과를 보고 조정\n* 관련: 「2026 주요업무계획」\n"
r = M.lint(ok)
check("정상 개조식 원고 위반 0", r["summary"]["ok"] and r["summary"]["total"] == 0, str(r["findings"][:2]))
check("'~있음' 종결은 서술형 아님", "DA_ENDING" not in rules("- 관내 농가 중 여건을 갖춘 농가는 소수 있음"))
for name, sample in (("geomto", geomto.SAMPLE), ("yoyak", yoyak.SAMPLE)):
    rr = M.lint(sample)
    check(f"{name} 샘플 위반 0", rr["summary"]["ok"], str([f["text"] for f in rr["findings"] if f["severity"] == "error"][:2]))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
