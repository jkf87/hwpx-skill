#!/usr/bin/env python3
"""이미지 편집 (P13) 회귀 테스트 — list/resize/replace/delete.

실제 이미지가 든 한컴 저장본 assets/gyehoek-reference.hwpx 사용.
사용법: python3 tests/test_image_edit.py
"""
import json
import struct
import subprocess
import sys
import tempfile
import zlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILL = ROOT / "scripts" / "fill_hwpx.py"
BASE = ROOT / "assets" / "gyehoek-reference.hwpx"   # 이미지 3개 보유

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
    out = json.loads(r.stdout) if r.stdout.strip() else None
    return r.returncode, out


def make_png(path):
    """stdlib로 2x2 빨강 PNG 생성(테스트용)."""
    w = h = 2
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(
            ">I", zlib.crc32(c) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def npics(p):
    return zipfile.ZipFile(p).read("Contents/section0.xml").decode().count("<hp:pic")


def crcs(p):
    return {i.filename: i.CRC for i in zipfile.ZipFile(p).infolist()}


def main():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        base_pics = npics(BASE)

        # ── list-images ──
        code, rep = run("list-images", BASE)
        check("list-images 성공 + 개수 일치",
              code == 0 and rep and rep["count"] == base_pics and base_pics > 0)
        check("이미지 항목에 index/크기",
              rep and all("index" in i and "width_mm" in i for i in rep["images"]))

        # ── resize (비율 유지) ──
        rs = d / "rs.hwpx"
        code, rep = run("resize-image", BASE, rs, "--index", "0",
                        "--width-mm", "30")
        check("resize 성공 + 폭 반영", code == 0 and abs(rep["width_mm"] - 30) < 0.5)
        check("resize: pic 수 불변", npics(rs) == base_pics)
        b, n = crcs(BASE), crcs(rs)
        check("resize: section0만 변경",
              sorted(k for k in b if b[k] != n.get(k)) == ["Contents/section0.xml"])
        code, _ = run("check", rs, "--strict")
        check("resize check --strict", code == 0)

        # ── replace ──
        png = d / "new.png"
        make_png(png)
        rp = d / "rp.hwpx"
        code, rep = run("replace-image", BASE, rp, "--index", "0", "--image", png)
        check("replace 성공 + 새 BinData", code == 0 and rep.get("new_item"))
        check("replace: pic 수 불변", npics(rp) == base_pics)
        check("replace: BinData 추가됨",
              any("BinData" in f and f not in {i for i in crcs(BASE)}
                  for f in crcs(rp)))
        code, _ = run("check", rp, "--strict")
        check("replace check --strict", code == 0)

        # ── delete ──
        dl = d / "dl.hwpx"
        code, rep = run("delete-image", BASE, dl, "--index", "0")
        check("delete 성공 + pic 1 감소", code == 0 and npics(dl) == base_pics - 1)
        code, _ = run("check", dl, "--strict")
        check("delete check --strict", code == 0)

        # ── 인덱스 초과 거부 ──
        code, _ = run("resize-image", BASE, d / "x.hwpx", "--index", "99",
                      "--width-mm", "10", expect=1)
        check("인덱스 초과 거부(exit 1)", code == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
