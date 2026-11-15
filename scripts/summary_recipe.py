#!/usr/bin/env python3
"""Optional plain-content recipe for a short A4 process summary.

Only consecutive 2-6-stage processes are supported. Content is not summarized,
truncated, or merged. The fixed layout is a starting point, not a one-page
guarantee. Use full one_shot v2 for other layouts or unresolved required facts.
"""

from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys

from jsonschema import Draft202012Validator
import one_shot

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas/process-summary.schema.json"
EXAMPLE_PATH = ROOT / "examples/process-summary-content.json"
V2_HINT = "Use scripts/one_shot.py with a full v2 specification for requirements outside this optional recipe."


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def pointer_part(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def validate_content(content):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(content),
        key=lambda e: str(list(e.absolute_path)),
    )
    if errors:
        messages = [
            "/" + "/".join(pointer_part(p) for p in e.absolute_path) + ": " + e.message
            for e in errors[:12]
        ]
        raise ValueError("; ".join(messages) + ". " + V2_HINT)

    # The underlying generator accepts Markdown. This optional recipe accepts
    # plain text so its parser must not silently interpret supplied characters.
    def visit(value, pointer=""):
        if isinstance(value, str):
            if value != value.strip():
                raise ValueError(
                    f"{pointer}: leading/trailing whitespace is not supported; no text was stripped. {V2_HINT}"
                )
            if re.search(
                r"(?:\*\*|\+\+|!!|__|~~|`|!\[|\]\(|<[^>]+>)", value
            ) or re.match(r"^(?:#{1,6}\s|>\s|[-*+]\s|\|)", value):
                raise ValueError(
                    f"{pointer}: formatting markup is outside this plain-text recipe. {V2_HINT}"
                )
            if any(token in value for token in (r"\n", r"\r", r"\t")):
                raise ValueError(
                    f"{pointer}: literal escape sequence; supply a single plain-text line or use full v2 for an intentional literal. {V2_HINT}"
                )
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, f"{pointer}/{i}")
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, pointer + "/" + pointer_part(key))

    visit(content)


def compile_content(content):
    """Return a deterministic v2 spec and an exact source->target text map."""
    validate_content(content)
    content = deepcopy(content)
    mappings = []

    def mapped(source, target, text, start=0):
        mappings.append(
            {
                "source": source,
                "target": target,
                "text": text,
                "target_start": start,
                "target_end": start + len(text),
            }
        )
        return text

    blocks = [
        {
            "type": "paragraph",
            "text": mapped("/lead", "/blocks/0/text", content["lead"]),
        }
    ]
    nodes = [
        {
            "id": f"stage_{i + 1}",
            "title": mapped(f"/stages/{i}", f"/blocks/1/nodes/{i}/title", title),
        }
        for i, title in enumerate(content["stages"])
    ]
    blocks.append(
        {
            "type": "diagram",
            "pattern": "process",
            "direction": "horizontal",
            "nodes": nodes,
            "edges": [
                {"from": a["id"], "to": b["id"]} for a, b in zip(nodes, nodes[1:])
            ],
        }
    )
    for i, section in enumerate(content["sections"]):
        index = len(blocks)
        blocks.append(
            {
                "type": "heading",
                "text": mapped(
                    f"/sections/{i}/title", f"/blocks/{index}/text", section["title"]
                ),
            }
        )
        blocks.append(
            {
                "type": "list",
                "items": [
                    mapped(
                        f"/sections/{i}/items/{j}",
                        f"/blocks/{index + 1}/items/{j}",
                        item,
                    )
                    for j, item in enumerate(section["items"])
                ],
            }
        )
    for i, note in enumerate(content.get("notes", [])):
        blocks.append(
            {
                "type": "paragraph",
                "text": mapped(f"/notes/{i}", f"/blocks/{len(blocks)}/text", note),
            }
        )
    source_text = "출처: "
    for i, source in enumerate(content["sources"]):
        if i:
            source_text += "; "
        source_text += mapped(
            f"/sources/{i}", f"/blocks/{len(blocks)}/text", source, len(source_text)
        )
    blocks.append({"type": "paragraph", "text": source_text})
    spec = {
        "version": 2,
        "kind": "brief-report",
        "output": "summary.hwpx",
        "title": mapped("/title", "/title", content["title"]),
        "profile": "neutral",
        "style": {"font": "맑은 고딕", "body_size": 11, "accent": "245A81"},
        "metadata": {"줄간격": "145"},
        "blocks": blocks,
        "quality": {
            "hancom": "off",
            "writing": "advisory",
            "repair_layout": False,
            "reject_placeholders": True,
        },
    }
    if "facts" in content:
        spec["facts"] = {
            key: mapped(
                "/facts/" + pointer_part(key), "/facts/" + pointer_part(key), text
            )
            for key, text in content["facts"].items()
        }
    # The engine's normal expected-text check is derived from its parser. Also
    # require the supplied prose so parser interpretation cannot silently drop it.
    spec["quality"]["required_text"] = list(
        dict.fromkeys(row["text"] for row in mappings)
    )
    mapping = {
        "version": 1,
        "recipe": "process-summary",
        "mappings": mappings,
        "relationship": "consecutive stages in supplied array order; no branching, skipping or cycles",
        "page_review": "not_run; short A4 layout is not a one-page guarantee",
    }
    return spec, mapping


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def write_new(path, text):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def execute_recipe(input_path, output_dir, *, argv):
    """Run once in a new directory, keeping evidence even on build failure.

    Returns exact CLI (exit code, stdout, stderr). A path refusal never writes
    into an existing directory; its failure evidence is returned on stdout.
    """
    input_path = Path(input_path).resolve()
    requested_output = Path(output_dir).absolute()
    engine_stdout, engine_stderr = io.StringIO(), io.StringIO()
    created = False
    input_hash = None
    out = requested_output
    report = None
    phase = "reserve_output_directory"
    try:
        if requested_output.exists() or requested_output.is_symlink():
            raise ValueError(
                "output-dir must be new; existing directories, files and symlinks are refused"
            )
        out = requested_output.resolve()
        if not out.parent.is_dir():
            raise ValueError("output-dir parent must already exist")
        # Atomic reservation also handles another writer winning after checks.
        out.mkdir(exist_ok=False)
        created = True
        phase = "read_input"
        raw = input_path.read_bytes()
        input_hash = hashlib.sha256(raw).hexdigest()
        with (out / "input.json").open("xb") as stream:
            stream.write(raw)
        phase = "validate_and_compile"
        content = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=reject_duplicate_keys
        )
        spec, mapping = compile_content(content)
        mapping["input_sha256"] = input_hash
        write_new(out / "spec.json", json_text(spec))
        write_new(out / "content-map.json", json_text(mapping))
        phase = "one_shot.build"
        with (
            contextlib.redirect_stdout(engine_stdout),
            contextlib.redirect_stderr(engine_stderr),
        ):
            report = one_shot.build(
                spec,
                spec_dir=out,
                spec_path=out / "spec.json",
                report_path=out / "quality.json",
            )
        phase = "save_quality"
    except Exception as exc:
        report = {
            "version": 1,
            "ok": False,
            "published": False,
            "status": "FAIL",
            "errors": [str(exc)],
            "failure_phase": phase,
            "hint": V2_HINT,
            "checks": {
                "render": {"status": "not_run"},
                "visual_review": {"status": "not_run"},
            },
        }
    exit_code = 0 if report.get("ok") and report.get("published") else 1
    report["recipe"] = {
        "name": "process-summary",
        "input_sha256": input_hash,
        "actual_page_count": None,
        "one_page_verified": False,
        "message": "Static checks do not verify one page. Render with Hancom and review all pages.",
    }
    report["report_saved"] = False
    if created:
        try:
            report["report_saved"] = True
            write_new(out / "quality.json", json_text(report))
        except OSError as exc:
            report["report_saved"] = False
            report["ok"] = False
            report["status"] = "PARTIAL" if report.get("published") else "FAIL"
            report["errors"].append(f"Could not save quality evidence: {exc}")
            exit_code = 2
    # This serialized object is the exact stdout subsequently emitted by main.
    # The field belongs to CLI outcome, not the earlier quality-gate snapshot.
    report["execution_log_saved"] = created
    stdout = json_text(report)
    stderr = ""
    if created:
        log = {
            "argv": list(argv),
            "input_path": str(input_path),
            "output_dir": str(out),
            "input_sha256": input_hash,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "engine_stdout": engine_stdout.getvalue(),
            "engine_stderr": engine_stderr.getvalue(),
            "engine_api": "one_shot.build",
        }
        try:
            write_new(out / "execution-log.json", json_text(log))
        except OSError as exc:
            stderr = f"Execution log could not be saved: {exc}\n"
            report["execution_log_saved"] = False
            report["ok"] = False
            report["status"] = "PARTIAL" if report.get("published") else "FAIL"
            report["errors"].append(stderr.rstrip())
            stdout = json_text(report)
            exit_code = 2
    return exit_code, stdout, stderr


def invocation_argv() -> list[str]:
    """실행 명령을 재현 가능하게 기록한다.

    3.10+ 는 sys.orig_argv 를 쓴다. macOS 프레임워크 빌드에서는 orig_argv[0] 이
    Python.app 내부 실행 파일이라 인터프리터 경로만 sys.executable 로 맞춘다.
    3.9 에는 orig_argv 가 없으므로 `-X utf8` 같은 인터프리터 옵션을 sys.flags 에서
    복원한다.
    """
    orig = getattr(sys, "orig_argv", None)
    if orig:
        return [sys.executable, *orig[1:]]
    flags = getattr(subprocess, "_args_from_interpreter_flags", list)()
    return [sys.executable, *flags, *sys.argv]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: python -X utf8 summary_recipe.py content.json --output-dir new-review-folder. --schema / --example print inputs without building.",
    )
    parser.add_argument("content", nargs="?", help="plain-content JSON")
    parser.add_argument(
        "--output-dir", help="new directory whose parent exists; never overwritten"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--schema", action="store_true")
    modes.add_argument("--example", action="store_true")
    args = parser.parse_args()
    if args.schema or args.example:
        if args.content or args.output_dir:
            parser.error("schema/example discovery cannot be combined with a build")
        print(
            (SCHEMA_PATH if args.schema else EXAMPLE_PATH).read_text(encoding="utf-8"),
            end="",
        )
        return 0
    if not args.content or not args.output_dir:
        parser.error("content and --output-dir are required for a build")
    code, stdout, stderr = execute_recipe(
        args.content,
        args.output_dir,
        argv=invocation_argv(),
    )
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
