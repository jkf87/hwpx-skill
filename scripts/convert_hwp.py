#!/usr/bin/env python3
"""HWP → HWPX 변환 스크립트.

claw-hwp와 동일한 vendored rhwp WASM 런타임을 사용하여 HWP(바이너리)를
HWPX(개방형 XML)로 변환한다.

사용법:
    python3 convert_hwp.py input.hwp [-o output.hwpx]
    python3 convert_hwp.py input.hwp --info   # 문서 정보만 출력

의존성:
    Node.js 18+
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, NamedTuple, Protocol, TypedDict, cast


SCRIPT_DIR = Path(__file__).resolve().parent
RHWP_CONVERTER = SCRIPT_DIR / "rhwp_convert.mjs"
VALIDATOR = SCRIPT_DIR / "validate.py"
STRICT_CHECKER = SCRIPT_DIR / "fill_hwpx.py"


class NormalizeFunction(Protocol):
    def __call__(
        self,
        source_file: BinaryIO,
        destination_path: Path,
        page_defs: list[PageDef] | None = None,
    ) -> None: ...


NORMALIZE_EXPORTED_HWPX = cast(
    NormalizeFunction,
    getattr(
        import_module(
            "scripts.hwpx_export_patch" if __package__ else "hwpx_export_patch"
        ),
        "normalize_exported_hwpx",
    ),
)


class ConversionError(RuntimeError):
    pass


class PageDef(TypedDict):
    width: int
    height: int
    marginLeft: int
    marginRight: int
    marginTop: int
    marginBottom: int
    marginHeader: int
    marginFooter: int
    marginGutter: int
    landscape: bool
    binding: int


class CliArgs(NamedTuple):
    input_path: str
    output_path: str | None
    show_info: bool
    as_json: bool
    keep_char_borders: bool


class InfoResult(TypedDict):
    title: str | None
    author: str | None
    subject: str | None
    keywords: str | None
    version: str
    format: str
    section_count: int
    page_count: int
    paragraph_count: int
    embedded_bindata_count: int | None
    validation_warning_count: int
    metadata_available: bool


def _run_checked(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise ConversionError(message)
    return completed.stdout


def _export_hwpx(source: Path, destination: BinaryIO) -> None:
    completed = subprocess.run(
        ["node", str(RHWP_CONVERTER), "--stdout", str(source)],
        stdout=destination,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise ConversionError(message)
    destination.flush()
    os.fsync(destination.fileno())


def _page_defs(source: Path) -> list[PageDef]:
    raw = _run_checked(["node", str(RHWP_CONVERTER), "--layout-info", str(source)])
    result = json.loads(raw)
    if not isinstance(result, list) or not result:
        raise ConversionError("rhwp returned malformed page geometry")
    return cast(list[PageDef], result)


def _output_mode(destination: Path) -> int:
    if destination.exists():
        return stat.S_IMODE(destination.stat().st_mode)
    previous_umask = os.umask(0)
    _ = os.umask(previous_umask)
    return 0o666 & ~previous_umask


def convert(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str] | None = None,
    fix_char_borders: bool = True,
    fix_text_direction: bool = True,
) -> str:
    """HWP 파일을 HWPX로 변환.

    Args:
        input_path: 입력 .hwp 파일 경로
        output_path: 출력 .hwpx 파일 경로 (기본: 같은 이름 .hwpx)
        fix_char_borders: 이전 API 호환용. rhwp 경로에서는 별도 보정을 하지 않음.
        fix_text_direction: 이전 API 호환용. rhwp 경로에서는 별도 보정을 하지 않음.
    Returns:
        출력 파일 경로
    """
    source = Path(input_path)
    destination = Path(output_path) if output_path else source.with_suffix(".hwpx")
    _ = fix_char_borders, fix_text_direction
    if source.resolve() == destination.resolve():
        raise ConversionError("output path must differ from the input HWP path")
    mode = _output_mode(destination)
    page_defs = _page_defs(source)
    with tempfile.TemporaryDirectory(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    ) as staging_name:
        staging = Path(staging_name)
        exported_path = staging / "exported.hwpx"
        temporary = staging / "normalized.hwpx"
        with exported_path.open("x+b") as exported_file:
            _export_hwpx(source, exported_file)
            NORMALIZE_EXPORTED_HWPX(exported_file, temporary, page_defs)
        _ = _run_checked([sys.executable, str(VALIDATOR), str(temporary)])
        _ = _run_checked(
            [sys.executable, str(STRICT_CHECKER), "check", str(temporary), "--strict"]
        )
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    return str(destination)


def info(input_path: str | os.PathLike[str]) -> InfoResult:
    """HWP 파일의 메타데이터를 딕셔너리로 반환."""
    raw = _run_checked(["node", str(RHWP_CONVERTER), "--info", str(input_path)])
    fields = raw.splitlines()
    if len(fields) != 6:
        raise ConversionError("rhwp returned malformed document information")
    return InfoResult(
        title=None,
        author=None,
        subject=None,
        keywords=None,
        version=fields[1],
        format=fields[0],
        section_count=int(fields[2]),
        page_count=int(fields[3]),
        paragraph_count=int(fields[4]),
        embedded_bindata_count=None,
        validation_warning_count=int(fields[5]),
        metadata_available=False,
    )


def _usage() -> str:
    return (
        "usage: convert_hwp.py INPUT [-o OUTPUT] [--info] [--json] "
        "[--keep-char-borders]\n"
        "\nHWP(바이너리) → HWPX(개방형 XML) 변환\n"
    )


def _parse_cli(argv: list[str]) -> CliArgs | None:
    if "-h" in argv or "--help" in argv:
        print(_usage(), end="")
        return None

    output_path: str | None = None
    input_path: str | None = None
    show_info = False
    as_json = False
    keep_char_borders = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument.startswith("--output="):
            output_path = argument.split("=", 1)[1]
            if not output_path:
                raise ConversionError("--output requires a path")
        elif argument in ("-o", "--output"):
            index += 1
            if index >= len(argv):
                raise ConversionError(f"{argument} requires a path")
            output_path = argv[index]
        elif argument == "--info":
            show_info = True
        elif argument == "--json":
            as_json = True
        elif argument == "--keep-char-borders":
            keep_char_borders = True
        elif argument.startswith("-"):
            raise ConversionError(f"unknown option: {argument}")
        elif input_path is None:
            input_path = argument
        else:
            raise ConversionError("multiple input files are not supported")
        index += 1

    if input_path is None:
        raise ConversionError("input HWP path is required")
    return CliArgs(input_path, output_path, show_info, as_json, keep_char_borders)


def main() -> int:
    try:
        args = _parse_cli(sys.argv[1:])
        if args is None:
            return 0
        if not os.path.exists(args.input_path):
            raise ConversionError(f"파일을 찾을 수 없습니다: {args.input_path}")
        if not args.input_path.lower().endswith(".hwp"):
            print(f"경고: .hwp 파일이 아닙니다: {args.input_path}", file=sys.stderr)

        if args.show_info:
            result = info(args.input_path)
            if args.as_json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for k, v in result.items():
                    print(f"  {k}: {v}")
        else:
            output = convert(
                args.input_path,
                args.output_path,
                fix_char_borders=not args.keep_char_borders,
            )
            if args.as_json:
                print(
                    json.dumps(
                        {
                            "input": args.input_path,
                            "output": output,
                            "size": os.path.getsize(output),
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                print(f"변환 완료: {args.input_path} → {output}")
                print(f"  크기: {os.path.getsize(output):,} bytes")
    except (ConversionError, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
