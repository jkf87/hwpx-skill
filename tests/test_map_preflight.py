#!/usr/bin/env python3
"""map_preflight.py — 치환 맵 사전검증기 / 잔재 대조기 회귀 테스트.

실제로 났던 사고를 고정한다:
  · '‧'(U+2027) vs '·'(U+00B7) 혼동으로 키가 조용히 안 맞던 것
  · 여러 문단을 한 키로 합쳐 써서 영영 매칭 안 되던 것
  · dump 주석이 키에 딸려 들어가 매칭이 깨지던 것
  · 맵에 안 넣은 문단에 원본이 남은 채 배포되던 것
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MP = ROOT / "scripts" / "map_preflight.py"
FILL = ROOT / "scripts" / "fill_hwpx.py"
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


def write_map(d, path):
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def main():
    tmp = Path(tempfile.mkdtemp())

    # dump: 마지막 탭 뒤가 곧 키여야 한다(주석이 섞이면 안 됨)
    r = run(MP, "dump", REF, "--grep", "해빙기취약시설")
    line = r.stdout.strip().splitlines()[0]
    idx, flag, text = line.split("\t", 2)
    check(flag in ("OK", "CTRL"), "dump: 2번째 컬럼이 OK/CTRL 플래그")
    check("[!" not in text, "dump: 텍스트 컬럼에 주석이 섞이지 않음")

    # 그 텍스트를 그대로 키로 쓰면 preflight 를 통과해야 한다
    m = tmp / "m1.json"
    write_map({text: "치환됨"}, m)
    r = run(MP, "check", REF, "--map", m)
    check(r.returncode == 0, "dump 텍스트를 키로 쓰면 preflight 통과")

    # 혼동문자(‧ vs ·) 불일치 → 교정필요로 잡고 실제 원문을 제시
    bad = text.replace("·", "‧") if "·" in text else text.replace("‧", "·")
    check(bad != text, "혼동문자 케이스 생성됨")
    m2 = tmp / "m2.json"
    write_map({bad: "치환됨"}, m2)
    r = run(MP, "check", REF, "--map", m2)
    check(r.returncode == 2, "혼동문자 키는 차단(exit 2)")
    check("교정필요 1" in r.stdout, "혼동문자를 '교정필요'로 분류")
    check(text in r.stdout, "실제 원문을 교정안으로 제시")

    # --fix 로 교정된 맵을 쓰면 통과 + 실제 replace 성공
    fixed = tmp / "fixed.json"
    run(MP, "check", REF, "--map", m2, "--fix", fixed)
    r = run(MP, "check", REF, "--map", fixed)
    check(r.returncode == 0, "--fix 로 교정된 맵은 preflight 통과")
    out = tmp / "out.hwpx"
    r = run(FILL, "replace", REF, out, "--map", fixed)
    check(r.returncode == 0, "교정된 맵으로 replace 성공")
    check(json.loads(r.stdout)["not_found"] == [], "replace not_found 없음")

    # 못 찾는 키가 있으면 replace 는 기본적으로 차단된다
    m3 = tmp / "m3.json"
    write_map({text: "치환됨", "문서에 절대 없는 문구 XYZ": "무시"}, m3)
    r = run(FILL, "replace", REF, tmp / "o3.hwpx", "--map", m3)
    check(r.returncode == 2, "미매칭 키가 있으면 replace 차단(exit 2)")
    check("치환 실패" in r.stderr, "차단 시 원인 진단을 stderr 로 출력")
    r = run(FILL, "replace", REF, tmp / "o4.hwpx", "--map", m3,
            "--allow-unmatched")
    check(r.returncode == 0, "--allow-unmatched 면 통과")

    # residue: 원본 자신을 대조하면 잔재가 잡혀야 하고,
    # 전부 치환한 결과에는 그 문단이 없어야 한다
    r = run(MP, "residue", REF, "--against", REF)
    check(r.returncode == 2, "원본 자기 자신은 잔재로 검출(exit 2)")
    r = run(MP, "residue", out, "--against", REF)
    check(text not in r.stdout, "치환된 문단은 잔재 목록에서 사라짐")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
