"""Hancom PDF + page PNG evidence. Rendering never implies visual approval."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from hancom_worker import run_hancom


def page_count_check(expected, actual=None):
    return {
        "expected": expected,
        "actual": actual,
        "status": "not_run"
        if actual is None
        else ("passed" if actual == expected else "failed"),
        "ok": None if actual is None else actual == expected,
    }


def render_document(source: Path, output: Path, expected_pages=None) -> dict:
    source = source.resolve()
    output = output.resolve()
    if expected_pages is not None and (
        type(expected_pages) is not int or expected_pages < 1
    ):
        raise ValueError("expected-pages must be a positive integer")
    if not source.is_file() or source.suffix.lower() != ".hwpx":
        raise ValueError("source must be an existing HWPX file")
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "render output directory must be new or empty; preserve earlier evidence"
        )
    poppler = shutil.which("pdftoppm")
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / "document.pdf"
    report = run_hancom(source, pdf=pdf)
    report.update(
        source=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        visual_review={"status": "not_run", "ok": None},
    )
    if report["ok"]:
        report["pdf_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if poppler:
            subprocess.run(
                [poppler, "-scale-to", "1600", "-png", str(pdf), str(output / "page")],
                check=True,
                capture_output=True,
                timeout=45,
            )
            report["page_images"] = [str(p) for p in sorted(output.glob("page-*.png"))]
            report["ok"] = bool(report["page_images"])
        else:
            report["page_images"] = []
            report["page_images_status"] = "not_run"
            report["message"] = "PDF exported; pdftoppm unavailable, so no PNG pages."
    report["render_ok"] = report["ok"]
    if expected_pages is not None:
        actual = None
        if report["render_ok"]:
            actual = len(report["page_images"]) if poppler else report.get("pages")
        report["page_count_check"] = page_count_check(expected_pages, actual)
        if actual is not None and actual != expected_pages:
            report.update(
                ok=False,
                status="failed",
                message=f"Expected {expected_pages} pages, rendered {actual}.",
            )
    (output / "render.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--expected-pages", type=int, help="explicit user-requested page count"
    )
    args = parser.parse_args()
    try:
        result = render_document(args.source, args.output_dir, args.expected_pages)
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "message": str(exc),
            "visual_review": {"status": "not_run"},
            "render_ok": False,
            **(
                {"page_count_check": page_count_check(args.expected_pages)}
                if args.expected_pages is not None
                else {}
            ),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 2)
