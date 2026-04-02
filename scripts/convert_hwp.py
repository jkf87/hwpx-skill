#!/usr/bin/env python3
"""HWP → HWPX 변환 스크립트.

hwp2hwpx-python-refactor 패키지를 사용하여 HWP(바이너리) 파일을
HWPX(개방형 XML) 파일로 변환한다.

사용법:
    python3 convert_hwp.py input.hwp [-o output.hwpx]
    python3 convert_hwp.py input.hwp --info   # 문서 정보만 출력

의존성:
    pip install pyhwp5 olefile lxml --break-system-packages
"""

import argparse
import json
import os
import subprocess
import sys


def _ensure_dependencies():
    """필수 패키지 확인 및 자동 설치."""
    missing = []
    try:
        import olefile  # noqa: F401
    except ImportError:
        missing.append("olefile")
    try:
        import hwp5  # noqa: F401
    except ImportError:
        missing.append("pyhwp5")
    try:
        import lxml  # noqa: F401
    except ImportError:
        missing.append("lxml")

    if missing:
        print(f"[convert_hwp] 필수 패키지 설치 중: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--break-system-packages"]
            + missing,
            stdout=subprocess.DEVNULL,
        )


def _ensure_hwp2hwpx():
    """hwp2hwpx 패키지를 import 경로에 추가."""
    try:
        import hwp2hwpx  # noqa: F401
        return
    except ImportError:
        pass

    # 같은 사용자의 로컬 클론이 있으면 사용
    candidates = [
        os.path.expanduser("~/원자력연구원-claudecode/hwp2hwpx-python-refactor"),
        os.path.join(os.path.dirname(__file__), "..", "..", "hwp2hwpx-python-refactor"),
    ]
    for path in candidates:
        full = os.path.abspath(path)
        if os.path.isdir(os.path.join(full, "hwp2hwpx")):
            sys.path.insert(0, full)
            try:
                import hwp2hwpx  # noqa: F401
                return
            except ImportError:
                pass

    # GitHub에서 클론
    clone_dir = os.path.join(os.path.dirname(__file__), "..", ".hwp2hwpx-repo")
    clone_dir = os.path.abspath(clone_dir)
    if not os.path.isdir(os.path.join(clone_dir, "hwp2hwpx")):
        print("[convert_hwp] hwp2hwpx 레포 클론 중...")
        subprocess.check_call(
            ["git", "clone", "--depth", "1",
             "https://github.com/jkf87/hwp2hwpx-python-refactor.git",
             clone_dir],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    sys.path.insert(0, clone_dir)
    import hwp2hwpx  # noqa: F401


def convert(input_path, output_path=None):
    """HWP 파일을 HWPX로 변환.

    Args:
        input_path: 입력 .hwp 파일 경로
        output_path: 출력 .hwpx 파일 경로 (기본: 같은 이름 .hwpx)

    Returns:
        출력 파일 경로
    """
    _ensure_dependencies()
    _ensure_hwp2hwpx()
    from hwp2hwpx import convert_file
    return convert_file(input_path, output_path)


def info(input_path):
    """HWP 파일의 메타데이터를 딕셔너리로 반환."""
    _ensure_dependencies()
    _ensure_hwp2hwpx()
    from hwp2hwpx.reader import HWPReader

    with HWPReader(input_path) as reader:
        summary = reader.get_summary_info()
        fh = reader.get_file_header()
        section_count = reader.get_section_count()
        bin_data_list = reader.get_bin_data_list()

    result = {
        "title": summary.get("title", ""),
        "author": summary.get("author", ""),
        "subject": summary.get("subject", ""),
        "keywords": summary.get("keywords", ""),
        "version": f"{fh['major']}.{fh['minor']}.{fh['micro']}.{fh['build']}",
        "section_count": section_count,
        "embedded_bindata_count": len(bin_data_list),
    }
    create_time = summary.get("create_time")
    if create_time:
        result["create_time"] = str(create_time)
    mod_time = summary.get("last_saved_time")
    if mod_time:
        result["last_saved_time"] = str(mod_time)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="HWP(바이너리) → HWPX(개방형 XML) 변환"
    )
    parser.add_argument("input", help="입력 .hwp 파일 경로")
    parser.add_argument("-o", "--output", help="출력 .hwpx 파일 경로 (기본: 같은 이름)")
    parser.add_argument("--info", action="store_true", help="문서 정보만 출력 (변환 안 함)")
    parser.add_argument("--json", action="store_true", help="JSON 형태로 출력")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"오류: 파일을 찾을 수 없습니다: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not args.input.lower().endswith(".hwp"):
        print(f"경고: .hwp 파일이 아닙니다: {args.input}", file=sys.stderr)

    try:
        if args.info:
            result = info(args.input)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for k, v in result.items():
                    print(f"  {k}: {v}")
        else:
            output = convert(args.input, args.output)
            if args.json:
                print(json.dumps({"input": args.input, "output": output,
                                   "size": os.path.getsize(output)},
                                  ensure_ascii=False))
            else:
                print(f"변환 완료: {args.input} → {output}")
                print(f"  크기: {os.path.getsize(output):,} bytes")
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
