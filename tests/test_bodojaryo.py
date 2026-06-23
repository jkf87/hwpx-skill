#!/usr/bin/env python3
"""bodojaryo.py(정부 표준 보도자료, 레퍼런스 복제 방식) 양식 고정 테스트.

assets/bodojaryo-reference.hwpx를 복제해 표·이미지(로고)를 보존하고 본문/필드만
교체하는지 검증한다. 양식이 깨지지(표/이미지 수가 바뀌지) 않도록 잠그는 회귀 테스트.

사용법: python3 tests/test_bodojaryo.py
"""
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import bodojaryo  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


print("[reference]")
check("assets/bodojaryo-reference.hwpx 존재", bodojaryo.REF.exists())
with zipfile.ZipFile(bodojaryo.REF) as z:
    ref_names = z.namelist()
    ref_sec = z.read("Contents/section0.xml").decode("utf-8")
ref_tbl = ref_sec.count("<hp:tbl")
ref_pic = ref_sec.count("<hp:pic")
ref_bin = len([n for n in ref_names if n.startswith("BinData/")])
check("레퍼런스 표 5개", ref_tbl == 5, detail=f"={ref_tbl}")
check("레퍼런스 이미지 6개", ref_pic == 6 and ref_bin == 6, detail=f"pic={ref_pic} bin={ref_bin}")

print("[generate — 양식 보존 + 내용 교체]")
with tempfile.TemporaryDirectory() as d:
    out = Path(d) / "bodo.hwpx"
    bodojaryo.generate(bodojaryo.SAMPLE, out)
    check("생성 파일 존재", out.exists())
    with zipfile.ZipFile(out) as z:
        x = z.read("Contents/section0.xml").decode("utf-8")
        names = z.namelist()
        prv = z.read("Preview/PrvText.txt").decode("utf-8")
    # 양식(표/이미지) 보존
    check("표 5개 보존", x.count("<hp:tbl") == 5, detail=str(x.count("<hp:tbl")))
    check("이미지 6개(pic) 보존", x.count("<hp:pic") == 6, detail=str(x.count("<hp:pic")))
    check("BinData 6개(로고) 보존",
          len([n for n in names if n.startswith("BinData/")]) == 6)
    # 원본(철도) 내용 제거
    for token in ["철도의 날", "우정훈", "철도국", "철도정책과", "김재돈", "박동준"]:
        check(f"원본 잔재 제거: {token}", token not in x, detail="남아있음")
    # 새 내용 반영
    check("새 제목 반영", "공문서 작성 표준화 교육" in x)
    check("새 담당부서 반영", "정보공개제도과" in x and "디지털정부국" in x)
    check("새 담당자 반영", "홍길동" in x and "김철수" in x and "이영희" in x)
    # 본문 마커 재생성 (□×2, ㅇ×2, *×1)
    check("본문 □ 2개", x.count("<hp:t>□") == 2, detail=str(x.count("<hp:t>□")))
    check("본문 ㅇ 2개", x.count("<hp:t>ㅇ") == 2, detail=str(x.count("<hp:t>ㅇ")))
    check("PrvText 새 본문 반영(raw 회피)", "공문서 작성 표준화" in prv)

    # lxml 있으면 한컴 호환 게이트까지
    try:
        import lxml  # noqa: F401
        r = subprocess.run([sys.executable, str(ROOT / "scripts/validate.py"), str(out)],
                           capture_output=True, text=True)
        check("validate VALID", "VALID" in r.stdout and "INVALID" not in r.stdout)
        r = subprocess.run([sys.executable, str(ROOT / "scripts/fill_hwpx.py"),
                            "check", str(out), "--strict"], capture_output=True, text=True)
        check("check --strict 통과(exit 0)", r.returncode == 0, detail=r.stderr[:160])
    except Exception:
        print("  ⊘ SKIP validate/check (lxml 미설치)")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
