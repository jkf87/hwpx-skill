#!/usr/bin/env python3
"""fill_hwpx.py place-seal / insert-image 스모크 테스트 (P12 직인/이미지 삽입).

이미지(직인/서명)를 BinData에 추가 + content.hpf 매니페스트 등록 + section에
<hp:pic> 참조 삽입하고, 원본 텍스트/표 보존 + 한컴 열림 게이트(check --strict)를
확인한다. 테스트용 PNG/JPG는 stdlib(zlib/base64)로 생성한다.

사용법: python3 tests/test_seal.py
종료 코드 0이면 전체 통과.
"""
import base64
import json
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
import xml.dom.minidom as minidom
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


def make_png(path, w=30, h=20, rgb=(200, 0, 0)):
    """stdlib만으로 유효한 RGB PNG 생성."""
    def chunk(typ, data):
        c = typ + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    idat = zlib.compress(raw, 9)
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


# 1x1 baseline JPEG (유효)
_JPEG_1x1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q==")


def sec0(path):
    return zipfile.ZipFile(path).read("Contents/section0.xml").decode()


def hpf(path):
    return zipfile.ZipFile(path).read("Contents/content.hpf").decode()


def crc_map(path):
    return {i.filename: i.CRC for i in zipfile.ZipFile(path).infolist()}


def well_formed(xml):
    try:
        minidom.parseString(xml)
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        form = d / "form.hwpx"
        subprocess.run([sys.executable, str(BUILD), str(form)],
                       check=True, capture_output=True)
        seal = d / "seal.png"
        make_png(seal, 30, 20)
        jpg = d / "sig.jpg"
        jpg.write_bytes(_JPEG_1x1)

        before_txt = sec0(form)

        # ── 1) place-seal : 본문 문구 옆 떠있는 직인 ───────────────────
        out1 = d / "seal_body.hwpx"
        code, rep = run("place-seal", form, out1,
                        "--image", seal, "--anchor", "작성자: 미정")
        check("place-seal 성공", code == 0 and rep and rep["ok"])
        check("BinData 엔트리 추가 보고",
              rep and rep.get("added_entries") == ["BinData/image1.png"])
        s1 = sec0(out1)
        check("section에 <hp:pic> 삽입", "<hp:pic" in s1)
        check("binaryItemIDRef=image1 참조", 'binaryItemIDRef="image1"' in s1)
        check("떠있는 배치 treatAsChar=0", 'treatAsChar="0"' in s1)
        check("겹침 textWrap=IN_FRONT_OF_TEXT",
              "IN_FRONT_OF_TEXT" in s1)
        check("BinData/image1.png 실제 존재",
              "BinData/image1.png" in zipfile.ZipFile(out1).namelist())
        check("매니페스트 등록",
              '<opf:item id="image1" href="BinData/image1.png"' in hpf(out1)
              and 'media-type="image/png"' in hpf(out1))
        check("section0.xml well-formed", well_formed(s1))
        check("원본 텍스트 보존(앵커 유지)", "작성자: 미정" in s1)
        # 표/셀 텍스트 보존
        for kw in ("입사 지원 신청서", "연락처", "010-0000-0000"):
            check(f"원본 보존: {kw}", kw in s1)
        # ZIP 무결성
        check("ZIP 무결성(testzip None)",
              zipfile.ZipFile(out1).testzip() is None)
        # 원본 보존: section0.xml + content.hpf만 변경, BinData만 추가
        be, oe = crc_map(form), crc_map(out1)
        changed = sorted(k for k in be if be[k] != oe.get(k))
        new = sorted(k for k in oe if k not in be)
        check("원본 보존: section0.xml+content.hpf만 변경",
              changed == ["Contents/content.hpf", "Contents/section0.xml"])
        check("새 엔트리: BinData/image1.png만",
              new == ["BinData/image1.png"])

        # ── 2) 한컴 열림 게이트 ────────────────────────────────────────
        code, rep = run("check", out1, "--strict")
        check("place-seal check --strict 통과", code == 0 and rep and rep["ok"])

        # ── 3) place-seal : 표 셀 문구 ────────────────────────────────
        out2 = d / "seal_cell.hwpx"
        code, rep = run("place-seal", form, out2,
                        "--image", seal, "--anchor", "성  명",
                        "--size-mm", 15, "--dx-mm", 2, "--dy-mm", -3)
        check("place-seal 셀 앵커 성공", code == 0 and rep and rep["ok"])
        s2 = sec0(out2)
        check("셀에도 <hp:pic> 삽입", "<hp:pic" in s2)
        check("표 구조 보존(<hp:tbl>)", "<hp:tbl" in s2)
        check("dx-mm 미세조정 반영(보고)",
              rep and abs(rep.get("dx_mm", 0)) > 0)
        code, rep = run("check", out2, "--strict")
        check("셀 직인 check --strict 통과", code == 0 and rep and rep["ok"])

        # ── 4) insert-image : 블록(새 문단) ───────────────────────────
        out3 = d / "img_block.hwpx"
        code, rep = run("insert-image", form, out3,
                        "--image", seal, "--after", "작성자: 미정",
                        "--size-mm", 30, 20)
        check("insert-image 블록 성공", code == 0 and rep and rep["ok"])
        check("placement=block", rep and rep.get("placement") == "block")
        s3 = sec0(out3)
        check("블록 이미지 <hp:pic>", "<hp:pic" in s3)
        check("블록은 인라인 treatAsChar=1", 'treatAsChar="1"' in s3)
        check("블록 size-mm 폭 30mm", rep and rep.get("width_mm") == 30.0)
        check("블록 well-formed", well_formed(s3))
        code, rep = run("check", out3, "--strict")
        check("블록 이미지 check --strict 통과",
              code == 0 and rep and rep["ok"])

        # ── 5) insert-image : --inline + --para ───────────────────────
        out4 = d / "img_inline.hwpx"
        code, rep = run("insert-image", form, out4,
                        "--image", seal, "--para", -1, "--inline")
        check("insert-image --inline 성공", code == 0 and rep and rep["ok"])
        check("placement=inline", rep and rep.get("placement") == "inline")
        code, rep = run("check", out4, "--strict")
        check("inline 이미지 check --strict 통과",
              code == 0 and rep and rep["ok"])

        # ── 6) JPG 지원 + 매니페스트 media-type ───────────────────────
        out5 = d / "seal_jpg.hwpx"
        code, rep = run("place-seal", form, out5,
                        "--image", jpg, "--anchor", "작성자: 미정")
        check("JPG place-seal 성공", code == 0 and rep and rep["ok"])
        check("JPG 매니페스트 media-type=image/jpeg",
              'media-type="image/jpeg"' in hpf(out5))
        check("JPG BinData 추가",
              "BinData/image1.jpg" in zipfile.ZipFile(out5).namelist())
        code, rep = run("check", out5, "--strict")
        check("JPG check --strict 통과", code == 0 and rep and rep["ok"])

        # ── 7) 실제 한컴 저장본(이미 이미지 보유)에 추가 — id 충돌 회피 ─
        asset = ROOT / "assets" / "gyehoek-reference.hwpx"
        if asset.exists():
            out6 = d / "asset_seal.hwpx"
            code, rep = run("place-seal", asset, out6,
                            "--image", seal, "--anchor", "기본 방향",
                            "--size-mm", 18)
            check("실제 한컴본 직인 성공", code == 0 and rep and rep["ok"])
            check("기존 image1-3 회피 → image4",
                  rep and rep.get("item_id") == "image4")
            check("실제 한컴본 ZIP 무결성",
                  zipfile.ZipFile(out6).testzip() is None)
            code, rep = run("check", out6, "--strict")
            check("실제 한컴본 직인 check --strict 통과",
                  code == 0 and rep and rep["ok"])

        # ── 8) 에러 처리 ───────────────────────────────────────────────
        err = d / "err.hwpx"
        code, _ = run("place-seal", form, err,
                      "--image", seal, "--anchor", "없는문구ZZZ", expect=1)
        check("앵커 없음 → exit 1", code == 1)
        check("실패 시 출력 미생성", not err.exists())
        code, _ = run("place-seal", form, err,
                      "--image", seal, "--anchor", "작성자: 미정",
                      "--size-mm", 0, expect=1)
        check("size-mm 0 → exit 1", code == 1)
        bad = d / "bad.gif"
        bad.write_bytes(b"not really")
        code, _ = run("place-seal", form, err,
                      "--image", d / "nope.png", "--anchor", "작성자: 미정",
                      expect=1)
        check("이미지 파일 없음 → exit 1", code == 1)

        # 원본 파일 자체는 불변
        check("원본 form.hwpx 불변", sec0(form) == before_txt)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
