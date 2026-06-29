#!/usr/bin/env python3
"""secure_fill.py 스모크 테스트 — PII 비경유 양식 채우기.

검증 항목:
  (a) fill 후 값이 실제로 출력 파일 텍스트에 들어갔는지
  (b) stdout/stderr 어디에도 원문 PII 가 없는지 (핵심)
  (c) verify 가 값을 마스킹해 보고하는지
  (d) 원본 보존 + check --strict exit 0
  (e) 포맷 변환기 (phone/rrn/date/upper/nospace) 가 칸 모양에 맞게 변환하는지
  (f) detect 가 키만 출력하고 PII 를 안 흘리는지
  (g) shred 가 프로필을 안전 삭제하는지

사용법: python3 tests/test_secure_fill.py
종료 코드 0이면 전체 통과.
"""
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECURE = ROOT / "scripts" / "secure_fill.py"
FILL = ROOT / "scripts" / "fill_hwpx.py"
BUILD = Path(__file__).resolve().parent / "build_test_form.py"

sys.path.insert(0, str(ROOT / "scripts"))
import fill_hwpx as engine  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def run(*args, expect=0):
    """secure_fill.py 호출 — (exit, parsed_stdout, raw_stdout, raw_stderr)."""
    r = subprocess.run([sys.executable, str(SECURE), *map(str, args)],
                       capture_output=True, text=True)
    if r.returncode != expect:
        print(f"    [exit {r.returncode}] {r.stderr.strip()[:200]}")
    out = None
    if r.stdout.strip():
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return r.returncode, out, r.stdout, r.stderr


def run_fill(*args, expect=0):
    r = subprocess.run([sys.executable, str(FILL), *map(str, args)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


# 원문 PII — fill 입력값. 어떤 출력에도 나타나선 안 됨.
PII = {
    "name": "홍길동",
    "phone_digits": "01098765432",
    "phone_fmt": "010-9876-5432",
    "rrn_digits": "9001011234567",
    "rrn_full": "900101-1234567",
    "addr": "서울특별시 강남구 테헤란로 123",
}


def assert_no_pii(label, *texts):
    """주어진 텍스트 묶음에 원문 PII 가 하나도 없는지."""
    blob = "\n".join(texts)
    leaked = [v for v in PII.values() if v in blob]
    check(f"{label}: 원문 PII 비노출", not leaked, f"leaked={leaked}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sf_test_"))
    form = tmp / "form.hwpx"
    subprocess.run([sys.executable, str(BUILD), str(form)],
                   capture_output=True, text=True, check=True)
    original_bytes = form.read_bytes()

    # 프로필: 라벨 매칭 키(성명/연락처/주소) + 포맷 변환.
    profile = {
        "성명": PII["name"],
        "연락처": {"value": PII["phone_digits"], "format": "phone"},
        "주소": PII["addr"],
    }
    prof_path = tmp / "profile.json"
    prof_path.write_text(json.dumps(profile, ensure_ascii=False),
                         encoding="utf-8")
    out = tmp / "out.hwpx"

    print("[1] detect — 키만, PII 비노출")
    code, rep, so, se = run("detect", form)
    check("detect exit 0", code == 0, f"exit={code}")
    check("detect 키 목록 존재", rep and rep.get("key_count", 0) > 0)
    keys = [k["key"] for k in (rep or {}).get("keys", [])]
    check("detect 에 성명 키 포함", "성명" in keys, f"keys={keys}")
    assert_no_pii("detect", so, se)

    print("[2] fill — 값 비출력")
    code, rep, so, se = run("fill", form, out, "--profile", prof_path)
    check("fill exit 0", code == 0, f"exit={code}")
    check("fill engine_ok", rep and rep.get("engine_ok") is True)
    check("fill filled_count >= 3", rep and rep.get("filled_count", 0) >= 3,
          f"count={rep and rep.get('filled_count')}")
    check("연락처 가 formatted_keys 에 포함",
          rep and "연락처" in rep.get("formatted_keys", []))
    assert_no_pii("fill stdout/stderr", so, se)

    print("[3] 값이 실제로 출력 파일에 들어갔는지")
    text = engine.extract_all_text(str(out))
    check("출력 파일에 성명 값 존재", PII["name"] in text)
    check("출력 파일에 변환된 전화번호 존재", PII["phone_fmt"] in text,
          "phone format ###-####-#### 미적용")
    check("출력 파일에 원본 전화 digits(미변환) 부재",
          PII["phone_digits"] not in text)

    print("[4] verify — 마스킹 보고")
    code, rep, so, se = run("verify", out, "--profile", prof_path)
    check("verify exit 0", code == 0, f"exit={code}")
    statuses = {v["key"]: v["status"] for v in (rep or {}).get("verified", [])}
    check("성명 FILLED", statuses.get("성명") == "FILLED", f"{statuses}")
    check("연락처 FILLED", statuses.get("연락처") == "FILLED")
    masks = {v["key"]: v.get("masked", "") for v in (rep or {}).get("verified", [])}
    check("성명 마스킹에 별표 포함", "*" in masks.get("성명", ""))
    assert_no_pii("verify stdout/stderr", so, se)

    print("[5] 원본 보존 + check --strict")
    check("원본 바이트 불변", form.read_bytes() == original_bytes)
    code, so, se = run_fill("check", out, "--strict")
    check("check --strict exit 0", code == 0, f"exit={code}\n{se[:200]}")
    # 변경 안 된 ZIP 엔트리는 바이트 동일해야 함.
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as z0, \
            zipfile.ZipFile(out) as z1:
        unchanged_ok = True
        for n in z0.namelist():
            if n.endswith("section0.xml"):
                continue
            if z0.read(n) != z1.read(n):
                unchanged_ok = False
                break
    check("비변경 ZIP 엔트리 바이트 동일", unchanged_ok)

    print("[6] 포맷 변환기 — 단위 검증 (in-process, 값 비출력 경로)")
    import secure_fill as sf
    check("phone 기본 ###-####-####",
          sf._format_value("01012345678", "phone") == "010-1234-5678")
    check("phone:dot",
          sf._format_value("01012345678", "phone:dot") == "010.1234.5678")
    check("rrn 기본 ######-#######",
          sf._format_value("9001011234567", "rrn") == "900101-1234567")
    check("rrn:masked",
          sf._format_value("9001011234567", "rrn:masked") == "900101-1******")
    check("date YYYY. M. D.",
          sf._format_value("2026-06-29", "date") == "2026. 6. 29.")
    check("upper", sf._format_value("abc", "upper") == "ABC")
    check("nospace", sf._format_value("a b  c", "nospace") == "abc")
    check("mask 패턴(#)",
          sf._format_value("01012345678", "###-####-####") == "010-1234-5678")
    check("마스킹 rrn 앞3자리만",
          sf._mask("900101-1234567") == "900***-*******",
          sf._mask("900101-1234567"))

    print("[7] 좌표(positional) 채우기 + verify")
    prof2 = {"성명": {"value": PII["name"], "table": 0, "row": 0, "col": 1}}
    prof2_path = tmp / "profile2.json"
    prof2_path.write_text(json.dumps(prof2, ensure_ascii=False),
                          encoding="utf-8")
    out2 = tmp / "out2.hwpx"
    code, rep, so, se = run("fill", form, out2, "--profile", prof2_path)
    check("positional fill exit 0", code == 0, f"exit={code}")
    check("positional filled_count >= 1", rep and rep.get("filled_count", 0) >= 1)
    assert_no_pii("positional fill", so, se)
    code, so, se = run_fill("check", out2, "--strict")
    check("positional check --strict exit 0", code == 0)

    print("[8] shred — 프로필 안전 삭제")
    victim = tmp / "victim.json"
    victim.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    code, rep, so, se = run("shred", victim)
    check("shred exit 0", code == 0)
    check("shred 후 파일 삭제됨", not victim.exists())
    assert_no_pii("shred", so, se)

    # 화이트리스트(cwd·홈·임시) 밖 경로는 거부 — 임의 파일 파괴 방지
    code, rep, so, se = run("shred", "/etc/sf_refuse_probe", expect=2)
    check("shred 화이트리스트 밖 경로 거부(exit 2)", code == 2)
    check("거부 사유 표기",
          bool(rep) and rep["shredded"][0].get("refused") is True)

    print("[9] fill --shred-profile — 채운 뒤 프로필 삭제")
    prof3 = tmp / "profile3.json"
    prof3.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    out3 = tmp / "out3.hwpx"
    code, rep, so, se = run("fill", form, out3, "--profile", prof3,
                            "--shred-profile")
    check("fill --shred-profile exit 0", code == 0)
    check("프로필 삭제됨", not prof3.exists())
    check("profile_shredded 플래그", rep and rep.get("profile_shredded") is True)
    assert_no_pii("fill --shred-profile", so, se)

    print(f"\n{'='*40}\n결과: {PASS} 통과, {FAIL} 실패")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
