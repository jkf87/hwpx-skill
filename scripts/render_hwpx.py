"""Page PNG evidence. Rendering never implies visual approval.

Engines:
- hancom: independent Hancom instance -> PDF -> pdftoppm PNG (Windows only).
- rhwp: vendored rhwp runtime -> SVG -> PNG. Approximate layout for macOS/Linux,
  not a Hancom render.
- auto: hancom on Windows, rhwp elsewhere.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from hancom_worker import run_hancom

SCRIPT_DIR = Path(__file__).resolve().parent
RHWP_RENDERER = SCRIPT_DIR / "render_rhwp.mjs"
ENGINES = ("auto", "hancom", "rhwp")
PAGE_LONG_SIDE_PX = 1600  # same as pdftoppm -scale-to 1600 below
RHWP_FIDELITY = (
    "Approximate rhwp layout, not a Hancom render. Line breaks and page count "
    "can differ, and overflowing table cells may be clipped instead of growing "
    "as they do in Hancom."
)


def page_count_check(expected, actual=None):
    return {
        "expected": expected,
        "actual": actual,
        "status": "not_run"
        if actual is None
        else ("passed" if actual == expected else "failed"),
        "ok": None if actual is None else actual == expected,
    }


def resolve_engine(engine: str) -> str:
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    if engine != "auto":
        return engine
    return "hancom" if os.name == "nt" else "rhwp"


def _root_svg_scaled(svg_text: str, scale: float) -> str:
    """Scale only the root <svg> width/height; viewBox keeps the drawing."""
    start = svg_text.find("<svg")
    end = svg_text.find(">", start)
    if start < 0 or end < 0:
        return svg_text
    head = svg_text[start:end]
    for attr in ("width", "height"):
        head = re.sub(
            rf'(\s{attr}=")([0-9.]+)(")',
            lambda m: f"{m.group(1)}{float(m.group(2)) * scale:.2f}{m.group(3)}",
            head,
            count=1,
        )
    return svg_text[:start] + head + svg_text[end:]


def svg_to_png(svg: Path, png: Path, size_px=None):
    """Rasterize one page. Returns the converter name, or None if none exists."""
    long_side = max(size_px["width"], size_px["height"]) if size_px else 0
    scale = PAGE_LONG_SIDE_PX / long_side if long_side else 2.0
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        subprocess.run(
            [rsvg, "-z", f"{scale:.4f}", "-b", "white", "-o", str(png), str(svg)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return "rsvg-convert"
    sips = shutil.which("sips") if sys.platform == "darwin" else None
    if sips:
        # sips rasterizes at the SVG's own size; enlarge a copy for legible text.
        with tempfile.TemporaryDirectory() as td:
            scaled = Path(td) / svg.name
            scaled.write_text(
                _root_svg_scaled(svg.read_text(encoding="utf-8"), scale),
                encoding="utf-8",
            )
            subprocess.run(
                [sips, "-s", "format", "png", str(scaled), "--out", str(png)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        return "sips"
    return None


def render_rhwp(source: Path, output: Path) -> dict:
    report = {"engine": "rhwp", "fidelity": RHWP_FIDELITY, "render_ok": False}
    node = shutil.which("node")
    if not node:
        return {
            **report,
            "ok": False,
            "status": "not_run",
            "message": "node unavailable; rhwp rendering needs Node.js",
        }
    run = subprocess.run(
        [node, str(RHWP_RENDERER), str(source), str(output)],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    if run.returncode != 0:
        return {
            **report,
            "ok": False,
            "status": "failed",
            "message": run.stderr.strip() or f"rhwp renderer exited {run.returncode}",
        }
    info = json.loads(run.stdout)
    svgs = [output / page["svg"] for page in info["pages"]]
    images, converter = [], None
    for page, svg in zip(info["pages"], svgs):
        png = svg.with_suffix(".png")
        converter = svg_to_png(svg, png, page.get("size_px"))
        if converter is None:
            break
        images.append(str(png))
    report.update(
        rhwp_version=info["rhwp_version"],
        source_format=info["source_format"],
        rendered_pages=info["page_count"],
        page_svgs=[str(svg) for svg in svgs],
        page_images=images,
        image_converter=converter,
        validation_warnings=info["validation_warnings"],
        render_ok=info["page_count"] > 0,
    )
    if not report["render_ok"]:
        report.update(ok=False, status="failed", message="rhwp rendered no pages")
    elif converter is None:
        report.update(
            ok=False,
            status="partial",
            message="SVG pages rendered, but no SVG-to-PNG converter was found "
            "(install librsvg for rsvg-convert).",
        )
    else:
        report.update(
            ok=True, status="passed", message="rhwp render succeeded (approximate)."
        )
    return report


def render_document(
    source: Path, output: Path, expected_pages=None, engine: str = "hancom"
) -> dict:
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
    engine = resolve_engine(engine)
    if engine == "rhwp":
        output.mkdir(parents=True, exist_ok=True)
        report = render_rhwp(source, output)
        report.update(
            source=str(source),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            visual_review={"status": "not_run", "ok": None},
        )
        return _finish(report, output, expected_pages)
    poppler = shutil.which("pdftoppm")
    if not poppler:
        return {
            "ok": False,
            "status": "not_run",
            "message": "pdftoppm unavailable",
            "visual_review": {"status": "not_run"},
            **(
                {"page_count_check": page_count_check(expected_pages)}
                if expected_pages is not None
                else {}
            ),
        }
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / "document.pdf"
    report = run_hancom(source, pdf=pdf)
    report.update(
        source=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        visual_review={"status": "not_run", "ok": None},
    )
    if report["ok"]:
        subprocess.run(
            [poppler, "-scale-to", "1600", "-png", str(pdf), str(output / "page")],
            check=True,
            capture_output=True,
            timeout=45,
        )
        report["page_images"] = [str(p) for p in sorted(output.glob("page-*.png"))]
        report["pdf_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
        report["ok"] = bool(report["page_images"])
    report["engine"] = "hancom"
    return _finish(report, output, expected_pages)


def _finish(report: dict, output: Path, expected_pages) -> dict:
    report.setdefault("render_ok", report["ok"])
    if expected_pages is not None:
        actual = (
            report.get("rendered_pages", len(report.get("page_images", [])))
            if report["render_ok"]
            else None
        )
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
    parser.add_argument(
        "--engine",
        choices=ENGINES,
        default="auto",
        help="hancom (Windows), rhwp (approximate, macOS/Linux), "
        "auto = hancom on Windows else rhwp",
    )
    args = parser.parse_args()
    try:
        result = render_document(
            args.source, args.output_dir, args.expected_pages, engine=args.engine
        )
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
