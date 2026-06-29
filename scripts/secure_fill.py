#!/usr/bin/env python3
"""secure_fill.py — PII 비경유 HWPX 양식 채우기.

개인정보(PII) 값이 모델(Claude)의 컨텍스트나 stdout/stderr/로그로 절대
새어나가지 않게 하면서 양식을 채운다. 값은 프로필 파일에서 **in-process**로
읽어 fill_hwpx.py 엔진으로 바로 전달되며, 어떤 경로로도 출력되지 않는다.
출력에는 키 이름·개수·성공여부·마스킹된 표기만 담긴다.

서브커맨드:
  detect <form.hwpx>
      채울 수 있는 key 목록만 출력 (analyze 래핑, 기존 값/PII 비출력).
  fill <form> <out> --profile profile.json [--shred-profile]
      프로필에서 값을 읽어 양식을 채움. 값/변환값 비출력.
  verify <out> --profile profile.json
      값 존재를 마스킹해 보고 (예: 홍**동, 901***-*******).
  shred <path> [--profile path ...]
      파일을 안전 삭제 (0으로 덮어쓴 뒤 unlink).

설계: Claude는 KEY 이름으로만 오케스트레이션한다. 이 도구가 VALUE를 파일에서
in-process로 읽어 엔진에 직접 넘기고, 결과는 개수/마스킹만 반환한다. 절대
값을 print 하지 않으며, 예외 메시지에도 값이 들어가지 않게 살균한다.

포맷 변환기 (칸 모양에 맞춰 값 자동 변환 — 값/변환값 모두 비출력):
  phone   ###-####-####          (style: hyphen|dot|space|intl|intl-paren|digits)
  rrn     ######-#######         (style: hyphen|digits|front|masked)
  date    YYYY. M. D.            (style: yyyy/yy/mm/dd/m/d 토큰)
  upper / lower / nospace / digits / mask:패턴(# 자리)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

# fill_hwpx.py 엔진을 in-process로 재사용 (값이 별도 프로세스/argv를 안 거치게).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import fill_hwpx as engine  # noqa: E402


# ─── 안전 출력 (절대 값 비포함) ──────────────────────────────────────

def _emit(obj):
    """요약 객체만 stdout으로. 호출부가 값을 안 넣을 책임."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


# ─── 프로필 로딩 (json 또는 "키: 값" txt) ────────────────────────────

def _load_profile(path):
    """프로필을 in-process로 읽어 dict 반환. 값은 호출부 밖으로 안 나감.

    JSON: {"성명": "홍길동", "연락처": {"value": "01012345678",
            "format": "phone"}, ...}  또는 {key: "값"}.
    TXT : "키: 값" 줄 (# 주석 허용).
    """
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if path.lower().endswith(".json"):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("프로필 JSON은 객체여야 합니다")
        return data
    obj = {}
    for line in raw.splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        i = t.find(":")
        if i < 0:
            continue
        k = t[:i].strip()
        v = t[i + 1:].strip()
        if k:
            obj[k] = v
    return obj


def _field(entry):
    """프로필 값을 (value, format, coords) 로 정규화.

    entry 가 str 이면 (그 문자열, None, None).
    entry 가 dict 이면 value/format 및 옵션 table/row/col/section 좌표.
    """
    if isinstance(entry, dict):
        value = entry.get("value", entry.get("val", ""))
        fmt = entry.get("format")
        coords = None
        if entry.get("table") is not None and entry.get("row") is not None \
                and entry.get("col") is not None:
            coords = {
                "table": entry["table"], "row": entry["row"],
                "col": entry["col"], "section": entry.get("section", 0),
            }
        return ("" if value is None else str(value), fmt, coords)
    return ("" if entry is None else str(entry), None, None)


# ─── 포맷 변환기 (값/변환값 모두 비출력) ─────────────────────────────

def _parse_ymd(v):
    d = re.sub(r"\D", "", str(v))
    if len(d) >= 8:
        y, m, day = d[:4], d[4:6], d[6:8]
    elif len(d) == 6:
        yy = int(d[:2])
        y = str(2000 + yy if yy <= 29 else 1900 + yy)
        m, day = d[2:4], d[4:6]
    else:
        return None
    return {"y": y, "yy": y[2:], "m": m, "d": day}


def _fmt_date(v, style):
    p = _parse_ymd(v)
    if not p:
        return v
    style = style or "yyyy. m. d."
    out = (style.replace("yyyy", p["y"]).replace("yy", p["yy"])
                .replace("mm", p["m"]).replace("dd", p["d"]))
    out = out.replace("m", str(int(p["m"]))).replace("d", str(int(p["d"])))
    return out


def _mask_digits(v, pattern):
    ds = re.sub(r"\D", "", str(v))
    it = iter(ds)
    return re.sub(r"#", lambda _m: next(it, ""), str(pattern))


def _fmt_phone(v, style):
    d = re.sub(r"\D", "", str(v))
    if len(d) < 9:
        return v
    a, b, c = d[:3], d[3:-4], d[-4:]
    if style == "digits":
        return d
    if style == "dot":
        return f"{a}.{b}.{c}"
    if style == "space":
        return f"{a} {b} {c}"
    if style == "intl":
        return f"+82-{d[1:3]}-{b}-{c}"
    if style == "intl-paren":
        return f"82)({d[1:3]})-{b}-{c}"
    return f"{a}-{b}-{c}"  # hyphen 기본 ###-####-####


def _fmt_rrn(v, style):
    d = re.sub(r"\D", "", str(v))
    if len(d) != 13:
        return v
    if style == "digits":
        return d
    if style == "front":
        return d[:6]
    if style == "masked":
        return f"{d[:6]}-{d[6]}******"
    return f"{d[:6]}-{d[6:]}"  # hyphen 기본 ######-#######


def _format_value(value, fmt):
    """profile 값을 칸 모양에 맞춰 변환. 절대 print 하지 않는다."""
    if not fmt:
        return value
    s = str(fmt)
    ci = s.find(":")
    kind = s[:ci] if ci >= 0 else s
    style = s[ci + 1:] if ci >= 0 else ""
    if kind == "date":
        return _fmt_date(value, style)
    if kind == "phone":
        return _fmt_phone(value, style)
    if kind == "rrn":
        return _fmt_rrn(value, style)
    if kind == "mask":
        return _mask_digits(value, style)
    if kind == "digits":
        return re.sub(r"\D", "", str(value))
    if kind == "upper":
        return str(value).upper()
    if kind == "lower":
        return str(value).lower()
    if kind == "nospace":
        return re.sub(r"\s+", "", str(value))
    # 접두사 없는 자유 형태
    if "#" in s:
        return _mask_digits(value, s)
    if re.search(r"(yyyy|yy|mm|dd)", s):
        return _fmt_date(value, s)
    return value


# ─── 마스킹 (verify 보고용) ──────────────────────────────────────────

def _mask(v):
    """값을 마스킹해 표기. 숫자형(주민/전화)은 앞 3자리만, 이름형은 첫·끝만."""
    v = str(v)
    digit_count = sum(c.isdigit() for c in v)
    if digit_count >= 6:
        shown, out = 0, []
        for c in v:
            if c.isalnum():
                if shown < 3:
                    out.append(c)
                    shown += 1
                else:
                    out.append("*")
            else:
                out.append(c)
        return "".join(out)
    chars = list(v)
    if len(chars) <= 1:
        return "*"
    if len(chars) == 2:
        return chars[0] + "*"
    return chars[0] + "*" * (len(chars) - 2) + chars[-1]


# ─── 서브커맨드 ──────────────────────────────────────────────────────

def cmd_detect(args):
    """채울 수 있는 key 목록만 출력 — 기존 값/PII 비출력."""
    report = engine.analyze_hwpx(args.form)
    t = report["targets"]
    keys = []
    for c in t.get("label_value_cells", []):
        keys.append({
            "key": c["key"], "label": c["label"], "section": c["section"],
            "table": c["table"], "row": c["row"], "col": c["col"],
            "empty": c["empty"],
        })
    inline_keys = [c["key"] for c in t.get("inline_labels", [])]
    checkbox_keys = [c["key"] for c in t.get("checkboxes", [])]
    bracket_keys = [c["key"] for c in t.get("bracket_blanks", [])]
    annotation_keys = [c["key"] for c in t.get("annotation_blanks", [])]
    _emit({
        "file": args.form,
        "key_count": (len(keys) + len(inline_keys) + len(checkbox_keys)
                      + len(bracket_keys) + len(annotation_keys)),
        "keys": keys,
        "inline_keys": inline_keys,
        "checkbox_keys": checkbox_keys,
        "bracket_keys": bracket_keys,
        "annotation_keys": annotation_keys,
        "note": "키 이름만 — 기존 값/PII 는 읽어 출력하지 않음",
    })
    return 0


def cmd_fill(args):
    """프로필에서 값을 in-process로 읽어 양식 채움. 값/변환값 비출력."""
    profile = _load_profile(args.profile)  # 값 — 절대 print 안 함
    values = {}
    cells = []
    attempted = []          # 키 이름만
    empty_keys = []
    formatted_keys = []
    for key, entry in profile.items():
        value, fmt, coords = _field(entry)
        if value == "":
            empty_keys.append(key)
            continue
        out_val = _format_value(value, fmt)  # in-tool 변환, 비출력
        if fmt:
            formatted_keys.append(key)
        if coords:
            cells.append({
                "table": coords["table"], "row": coords["row"],
                "col": coords["col"], "section": coords["section"],
                "value": out_val,
            })
        else:
            values[key] = out_val
        attempted.append(key)

    filled, unmatched, modified, cell_errors = engine.fill_hwpx(
        args.source, args.out, values or None, cells or None)
    # 엔진의 filled 엔트리에는 value 가 들어있으므로 라벨/개수만 추출.
    filled_count = len(filled)
    filled_labels = [str(f.get("label", "")) for f in filled]

    ok = filled_count > 0 and not cell_errors

    if args.shred_profile:
        _shred_path(args.profile)

    _emit({
        "out": args.out,
        "engine_ok": ok,
        "attempted_keys": attempted,
        "filled_count": filled_count,
        "filled_labels": filled_labels,
        "formatted_keys": formatted_keys,
        "empty_keys": empty_keys,
        "unmatched_keys": unmatched,
        "cell_error_count": len(cell_errors),
        "modified_entries": modified,
        "profile_shredded": bool(args.shred_profile),
        "note": "값/변환값은 어떤 경로로도 출력하지 않음 (키·개수·성공여부만)",
    })
    return 0 if ok else 2


def cmd_verify(args):
    """값 존재를 마스킹해 보고 — 원문 값 비출력."""
    profile = _load_profile(args.profile)
    text = engine.extract_all_text(args.out)  # 문서 텍스트 (in-process)
    verified = []
    for key, entry in profile.items():
        value, fmt, coords = _field(entry)
        if value == "":
            verified.append({"key": key, "status": "EMPTY_KEY"})
            continue
        out_val = _format_value(value, fmt)
        present = out_val in text
        verified.append({
            "key": key,
            "status": "FILLED" if present else "MISSING",
            "masked": _mask(out_val),
            **({"positional": True} if coords else {}),
        })
    _emit({
        "out": args.out,
        "verified": verified,
        "note": "값은 마스킹 — 원문 PII 비출력",
    })
    return 0


def _shred_roots():
    roots = [os.getcwd(), os.path.expanduser("~"), tempfile.gettempdir(),
             "/tmp", "/private/tmp"]
    out = []
    for r in roots:
        try:
            out.append(os.path.realpath(r))
        except OSError:
            pass
    return out


def _shred_allowed(path):
    """cwd·홈·임시 디렉토리 하위 경로만 shred 허용 (claw secure-fill.mjs 가드 포팅).

    shred는 파일을 0으로 덮어쓴 뒤 삭제하는 비가역 작업이라, 임의 경로 파괴를
    막기 위해 화이트리스트 밖 경로는 거부한다.
    """
    try:
        rp = os.path.realpath(path)
    except OSError:
        return False
    for root in _shred_roots():
        if rp == root or rp.startswith(root + os.sep):
            return True
    return False


def _shred_path(path):
    """파일을 0으로 덮어쓴 뒤 unlink. 실패는 조용히 무시(메시지에 값 비포함)."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def cmd_shred(args):
    """프로필/임시파일 안전 삭제."""
    paths = list(args.paths)
    for p in (args.profile or []):
        paths.append(p)
    shredded = []
    refused = 0
    for p in paths:
        if not _shred_allowed(p):
            refused += 1
            shredded.append({"path": p, "refused": True,
                             "reason": "cwd·홈·임시 디렉토리 밖 경로는 shred 거부"})
            continue
        existed = os.path.exists(p)
        _shred_path(p)
        shredded.append({"path": p, "existed": existed,
                         "gone": not os.path.exists(p)})
    _emit({"shredded": shredded})
    return 2 if refused else 0


# ─── argparse ────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        prog="secure_fill.py",
        description="PII 비경유 HWPX 양식 채우기 (detect → fill → verify → shred)")
    sub = p.add_subparsers(dest="command", required=True)

    p_det = sub.add_parser("detect", help="채울 수 있는 key 목록 출력 (PII 비출력)")
    p_det.add_argument("form")

    p_fill = sub.add_parser("fill", help="프로필 값으로 양식 채우기 (값 비출력)")
    p_fill.add_argument("source")
    p_fill.add_argument("out")
    p_fill.add_argument("--profile", required=True,
                        help="값 프로필(.json 또는 키:값 .txt) — in-process로만 읽음")
    p_fill.add_argument("--shred-profile", action="store_true",
                        help="채운 뒤 프로필을 안전 삭제")

    p_ver = sub.add_parser("verify", help="채움 결과를 마스킹해 검증")
    p_ver.add_argument("out")
    p_ver.add_argument("--profile", required=True)

    p_shr = sub.add_parser("shred", help="프로필/임시파일 안전 삭제")
    p_shr.add_argument("paths", nargs="*")
    p_shr.add_argument("--profile", action="append",
                       help="삭제할 프로필 경로 (반복 가능)")
    return p


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "detect":
            return cmd_detect(args)
        if args.command == "fill":
            return cmd_fill(args)
        if args.command == "verify":
            return cmd_verify(args)
        if args.command == "shred":
            return cmd_shred(args)
    except Exception as e:  # noqa: BLE001
        # 예외 메시지에 값이 섞일 수 있으므로 타입만 노출, 본문은 버림.
        sys.stderr.write(
            "오류: secure_fill 처리 실패 (%s) — 값 비노출을 위해 상세 생략\n"
            % type(e).__name__)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
