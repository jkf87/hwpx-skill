#!/usr/bin/env python3
"""fill_hwpx.py 스모크 테스트 — analyze/fill/replace/add-row/add-para/verify 전체 파이프라인.

사용법: python3 tests/test_fill_hwpx.py
종료 코드 0이면 전체 통과.
"""
import json
import subprocess
import sys
import tempfile
import zipfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILL = ROOT / "scripts" / "fill_hwpx.py"
BUILD = Path(__file__).resolve().parent / "build_test_form.py"

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
    r = subprocess.run([sys.executable, str(FILL), *map(str, args)],
                       capture_output=True, text=True)
    if r.returncode != expect:
        print(f"    [exit {r.returncode}] {r.stderr.strip()[:200]}")
    out = None
    if r.stdout.strip():
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return r.returncode, out


def _make_raw(src, dst):
    """linesegarray를 모두 제거해 '한컴 미경유 raw' 상태 재현."""
    import io
    buf = src.read_bytes()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = [n for n in zf.namelist() if re.search(r"section\d+\.xml$", n)]
        with zipfile.ZipFile(dst, "w") as zo:
            for item in zf.infolist():
                data = zf.read(item.filename)
                if item.filename in names:
                    txt = data.decode("utf-8")
                    txt = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "",
                                 txt, flags=re.DOTALL)
                    txt = re.sub(r"<hp:linesegarray\b[^>]*/>", "", txt)
                    data = txt.encode("utf-8")
                ct = (zipfile.ZIP_STORED if item.filename == "mimetype"
                      else zipfile.ZIP_DEFLATED)
                zo.writestr(item, data, compress_type=ct)


def _make_solid_char_border(src, dst):
    """base borderFill 2의 테두리를 NONE→SOLID로 바꿔 '글자테두리 버그' 재현."""
    import io
    buf = src.read_bytes()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        h = zf.read("Contents/header.xml").decode("utf-8")

    def fix(m):
        b = m.group(1)
        for side in ("left", "right", "top", "bottom"):
            b = b.replace(f'{side}Border type="NONE"',
                          f'{side}Border type="SOLID"')
        return b
    h2 = re.sub(r'(<hh:borderFill id="2"[^>]*>.*?</hh:borderFill>)', fix,
                h, flags=re.DOTALL)
    with zipfile.ZipFile(dst, "w") as zo:
        with zipfile.ZipFile(io.BytesIO(buf)) as zf:
            for item in zf.infolist():
                data = (h2.encode("utf-8")
                        if item.filename == "Contents/header.xml"
                        else zf.read(item.filename))
                ct = (zipfile.ZIP_STORED if item.filename == "mimetype"
                      else zipfile.ZIP_DEFLATED)
                zo.writestr(item, data, compress_type=ct)


def _break_secpr(src, dst):
    """정상 HWPX의 secPr 자식 요소를 제거해 '한컴 손상 문서' 상태 재현."""
    import io
    buf = src.read_bytes()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = [n for n in zf.namelist() if re.search(r"section\d+\.xml$", n)]
        xml = zf.read(names[0]).decode("utf-8")
    # secPr 내부를 빈 grid만 남기고 비워 pagePr/margin 제거
    broken = re.sub(r"(<hp:secPr\b[^>]*>).*?(</hp:secPr>)",
                    r"\1<hp:grid/>\2", xml, count=1, flags=re.DOTALL)
    with zipfile.ZipFile(dst, "w") as zo:
        with zipfile.ZipFile(io.BytesIO(buf)) as zf:
            for item in zf.infolist():
                data = (broken.encode("utf-8")
                        if item.filename == names[0] else zf.read(item.filename))
                ct = (zipfile.ZIP_STORED if item.filename == "mimetype"
                      else zipfile.ZIP_DEFLATED)
                zo.writestr(item, data, compress_type=ct)


def _make_vertical(src, dst):
    """셀 textDirection을 VERTICAL로 바꿔 'hwp2hwpx 세로 오변환' 상태 재현."""
    import io
    buf = src.read_bytes()
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = [n for n in zf.namelist() if re.search(r"section\d+\.xml$", n)]
        with zipfile.ZipFile(dst, "w") as zo:
            for item in zf.infolist():
                data = zf.read(item.filename)
                if item.filename in names:
                    data = data.decode("utf-8").replace(
                        'textDirection="HORIZONTAL"',
                        'textDirection="VERTICAL"').encode("utf-8")
                ct = (zipfile.ZIP_STORED if item.filename == "mimetype"
                      else zipfile.ZIP_DEFLATED)
                zo.writestr(item, data, compress_type=ct)


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)

        # ─ analyze ─
        code, rep = run("analyze", form)
        check("analyze 실행", code == 0)
        check("analyze 타겟 발견", rep and rep["target_count"] >= 9,
              f"(got {rep and rep['target_count']})")

        # ─ fill (라벨/체크박스/괄호/어노테이션/헤더행/인라인) ─
        values = {"성명": "홍길동", "연락처": "010-1234-5678",
                  "주소": "서울특별시 강남구", "한자": "洪吉童", "동의": "☑",
                  "일반통": "3", "품명": "노트북", "수량": "2", "작성자": "김철수"}
        vf = d / "values.json"
        vf.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        out1 = d / "out1.hwpx"
        code, rep = run("fill", form, out1, "--values", vf)
        check("fill 성공", code == 0 and rep["ok"])
        check("fill 9개 채움", rep and len(rep["filled"]) == 9,
              f"(got {rep and len(rep['filled'])})")
        check("fill unmatched 없음", rep and not rep["unmatched"])

        xml = zipfile.ZipFile(out1).read("Contents/section0.xml").decode()
        check("self-closing run에 charPr 유지 삽입",
              '<hp:run charPrIDRef="2"><hp:t>홍길동</hp:t></hp:run>' in xml)
        check("어노테이션 보존", "서울특별시 강남구 (한자：洪吉童)" in xml)
        check("체크박스", "☑동의 □비동의" in xml)
        check("괄호 빈칸", "일반(3)통" in xml)

        # linesegarray 외과 제거: 수정된 문단(연락처 값)은 제거,
        # 무수정 문단(동의여부 라벨/제목/base 기본 문단)은 보존
        lsa0 = len(re.findall(
            r"<hp:linesegarray",
            zipfile.ZipFile(form).read("Contents/section0.xml").decode()))
        lsa = len(re.findall(r"<hp:linesegarray", xml))
        check("fill 후 수정 문단 linesegarray만 제거", lsa == lsa0 - 1,
              f"(got {lsa0}→{lsa})")

        # ─ replace (run 경계를 넘는 문구 + 제목 문단 캐시 제거) ─
        mf = d / "map.json"
        mf.write_text(json.dumps({"2025년 기준으로": "2026년 6월 기준으로",
                                  "입사 지원 신청서": "경력직 채용 신청서"},
                                 ensure_ascii=False), encoding="utf-8")
        out2 = d / "out2.hwpx"
        code, rep = run("replace", out1, out2, "--map", mf)
        check("replace 성공 (run 경계)", code == 0 and rep["total"] == 2)
        xml = zipfile.ZipFile(out2).read("Contents/section0.xml").decode()
        lsa2 = len(re.findall(r"<hp:linesegarray", xml))
        check("replace 후 제목 문단 캐시 제거", lsa2 == lsa - 1,
              f"(got {lsa}→{lsa2})")

        # ─ add-row ─
        rf = d / "rows.json"
        rf.write_text('[["모니터","5"],["키보드","10"]]', encoding="utf-8")
        out3 = d / "out3.hwpx"
        code, rep = run("add-row", out2, out3, "--table", 1, "--rows", rf)
        check("add-row 성공", code == 0 and rep["ok"])
        xml = zipfile.ZipFile(out3).read("Contents/section0.xml").decode()
        m = re.search(r'<hp:tbl id="9200"[^>]*rowCnt="(\d+)"', xml)
        check("add-row rowCnt 갱신", m and m.group(1) == "4")
        ids = re.findall(r'<hp:p id="(\d+)"', xml)
        check("add-row 문단 id 고유", len(ids) == len(set(ids)))

        # add-row 셀 수 불일치 거부
        bf = d / "bad.json"
        bf.write_text('[["하나"]]', encoding="utf-8")
        code, _ = run("add-row", out3, d / "x.hwpx", "--table", 1,
                      "--rows", bf, expect=1)
        check("add-row 셀 수 불일치 거부", code == 1)

        # ─ add-para ─
        out4 = d / "out4.hwpx"
        code, rep = run("add-para", out3, out4, "--after", "작성자",
                        "--text", "자동 생성 문단입니다.")
        check("add-para 성공", code == 0 and rep["ok"])

        # ─ fill --cells (좌표 지정) ─
        cf = d / "cells.json"
        cf.write_text('[{"table":0,"row":1,"col":1,"value":"010-9999-8888"}]',
                      encoding="utf-8")
        out5 = d / "out5.hwpx"
        code, rep = run("fill", out4, out5, "--cells", cf)
        check("fill --cells 성공", code == 0 and rep["ok"])

        # ─ verify (값 존재 + 원본 보존) ─
        av = d / "all.json"
        av.write_text(json.dumps({
            "v1": "2026년 6월 기준으로", "v2": "모니터", "v3": "키보드",
            "v4": "010-9999-8888", "v5": "자동 생성 문단입니다.",
            "성명": "홍길동"}, ensure_ascii=False), encoding="utf-8")
        code, rep = run("verify", out5, "--values", av, "--original", form)
        check("verify 전체 통과", code == 0 and rep["ok"])
        check("섹션 외 엔트리 바이트 보존",
              rep and rep["changed_entries"] == ["Contents/section0.xml"])
        check("verify가 openable 점검 포함",
              rep and rep.get("openable", {}).get("ok") is True)

        # ─ check: secPr 완전(base 골격) + linesegarray 보유 → 통과 ─
        code, rep = run("check", form)
        check("check secPr 통과 (errors 없음)", not rep["errors"],
              f"(errors: {rep and rep.get('errors')})")
        check("check linesegarray 보유 폼 raw 아님",
              rep and rep["raw_llm_suspect"] is False)

        # ─ check: linesegarray 제거한 raw 변형 → raw 탐지 + strict 차단 ─
        raw_form = d / "raw.hwpx"
        _make_raw(form, raw_form)
        code, rep = run("check", raw_form)
        check("check raw 탐지 (기본은 warning, exit 0)",
              code == 0 and rep["raw_llm_suspect"] is True)
        code, rep = run("check", raw_form, "--strict", expect=2)
        check("check --strict raw 차단 (exit 2)", code == 2 and not rep["ok"])

        # ─ check: secPr 망가뜨린 파일 탐지 (한컴 '손상 문서' 사고 재현) ─
        broken = d / "broken.hwpx"
        _break_secpr(form, broken)
        code, rep = run("check", broken, expect=2)
        check("check 깨진 secPr 탐지 (exit 2)", code == 2 and not rep["ok"])
        check("check pagePr 누락 보고",
              rep and any("pagePr" in e for e in rep["errors"]))

        # ─ fix-borders: 글자 테두리(charPr borderFillIDRef) 제거, 표셀 보존 ─
        # base 헤더의 charPr은 모두 borderFillIDRef를 가짐(변환기 버그와 동일 구조)
        h_before = zipfile.ZipFile(form).read("Contents/header.xml").decode()
        n_cp = len(re.findall(r"<hh:charPr\b[^>]*borderFillIDRef", h_before))
        check("테두리 폼 준비됨", n_cp > 0, f"(charPr 테두리 {n_cp})")
        fixed = d / "fixed.hwpx"
        code, rep = run("fix-borders", form, fixed)
        check("fix-borders 글자 테두리 제거", code == 0 and rep["char_borders_removed"] == n_cp)
        hh = zipfile.ZipFile(fixed).read("Contents/header.xml").decode()
        ss = zipfile.ZipFile(fixed).read("Contents/section0.xml").decode()
        check("fix-borders charPr 테두리 0",
              len(re.findall(r"<hh:charPr\b[^>]*borderFillIDRef", hh)) == 0)
        check("fix-borders 표셀 테두리 보존",
              "<hp:tc" in ss and "borderFillIDRef" in ss)
        code, rep = run("fix-borders", fixed)
        check("fix-borders 재실행 안전(idempotent)",
              rep["char_borders_removed"] == 0)

        # ─ 글자 테두리 버그 탐지 + check --strict 차단 ─
        # base borderFill 2(NONE)를 SOLID로 바꿔 '글자테두리 버그' 재현
        bordered = d / "bordered.hwpx"
        _make_solid_char_border(form, bordered)
        code, rep = run("check", bordered)
        check("글자테두리 버그 탐지", rep and rep.get("char_border_bug") is True,
              f"(signals: {rep and rep.get('char_border_signals')})")
        code, rep = run("check", bordered, "--strict", expect=2)
        check("check --strict 글자테두리 차단", code == 2 and not rep["ok"])
        # fix-borders 후 버그 해소
        bfixed = d / "bfixed.hwpx"
        run("fix-borders", bordered, bfixed)
        code, rep = run("check", bfixed, "--strict")
        check("fix-borders 후 strict 통과", code == 0 and rep["ok"])

        # ─ 머리말·꼬리말·쪽번호 (P3: claw-hwp hwpx-edit 포팅) ─
        def sec0(p):
            return zipfile.ZipFile(p).read("Contents/section0.xml").decode()

        hdr = d / "hdr.hwpx"
        code, rep = run("set-header", form, hdr, "--text", "대외주의")
        check("set-header 삽입", code == 0 and rep["action"] == "inserted")
        s = sec0(hdr)
        check("머리말 1개 + 텍스트 + vertAlign TOP",
              s.count("<hp:header") == 1 and "대외주의" in s
              and 'vertAlign="TOP"' in s)

        hdr2 = d / "hdr2.hwpx"
        code, rep = run("set-header", hdr, hdr2, "--text", "대외비")
        check("set-header 갱신 (중복 안 생김)",
              code == 0 and rep["action"] == "updated")
        s = sec0(hdr2)
        check("머리말 여전히 1개, 텍스트 교체",
              s.count("<hp:header") == 1 and "대외비" in s
              and "대외주의" not in s)

        ftr = d / "ftr.hwpx"
        code, rep = run("set-footer", hdr2, ftr, "--text", "발행: 재단")
        check("set-footer 삽입 (vertAlign BOTTOM)",
              code == 0 and rep["action"] == "inserted"
              and 'vertAlign="BOTTOM"' in sec0(ftr))

        pn = d / "pn.hwpx"
        code, rep = run("set-pagenum", ftr, pn, "--where", "footer")
        check("set-pagenum 기존 꼬리말에 추가",
              code == 0 and rep["action"] == "added-to-existing")
        s = sec0(pn)
        check("쪽번호 autoNum 1개 + 꼬리말 중복 없음",
              s.count("<hp:autoNum num=") == 1 and s.count("<hp:footer") == 1)

        code, rep = run("check", pn, "--strict")
        check("머리말/꼬리말/쪽번호 후 strict 통과", code == 0 and rep["ok"])

        # 원본 보존 — section0.xml만 변경
        be = {i.filename: i.CRC for i in zipfile.ZipFile(form).infolist()}
        pe = {i.filename: i.CRC for i in zipfile.ZipFile(pn).infolist()}
        changed = [k for k in be if be[k] != pe.get(k)]
        check("머리말/꼬리말/쪽번호: section0만 변경",
              changed == ["Contents/section0.xml"]
              and not [k for k in pe if k not in be])

        rmf = d / "rmf.hwpx"
        code, rep = run("remove-footer", pn, rmf)
        check("remove-footer 제거", code == 0 and rep["removed"] >= 1)
        s = sec0(rmf)
        check("꼬리말+autoNum 제거, 머리말 보존",
              "<hp:footer" not in s and s.count("<hp:autoNum num=") == 0
              and "<hp:header" in s)

        rmh = d / "rmh.hwpx"
        code, rep = run("remove-header", rmf, rmh)
        check("remove-header 제거",
              code == 0 and "<hp:header" not in sec0(rmh))

        # ─ 세로쓰기 오변환 탐지 (check --strict가 잡아야) — 강사카드 사고 방지 ─
        vbroken = d / "vbroken.hwpx"
        _make_vertical(form, vbroken)
        code, rep = run("check", vbroken, expect=0)
        check("세로 오변환 탐지(vertical_misconvert)",
              rep and rep.get("vertical_misconvert") is True,
              f"(signals: {rep and rep.get('vertical_signals')})")
        code, rep = run("check", vbroken, "--strict", expect=2)
        check("check --strict 세로 오변환 차단", code == 2 and not rep["ok"])
        # 정상(가로) 폼은 통과
        code, rep = run("check", form, "--strict")
        check("정상 가로 폼은 세로탐지 미발동",
              code == 0 and not rep.get("vertical_misconvert"))

        # ─ 무매칭 → 원본 바이트 동일 ─
        nf = d / "nomatch.json"
        nf.write_text('{"존재하지않는라벨":"값"}', encoding="utf-8")
        out6 = d / "out6.hwpx"
        code, _ = run("fill", form, out6, "--values", nf, expect=2)
        check("무매칭 시 exit 2", code == 2)
        check("무매칭 시 원본 바이트 동일",
              form.read_bytes() == out6.read_bytes())

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
