#!/usr/bin/env node
// Render every page of an HWP/HWPX file to SVG with the vendored rhwp runtime.
// This is an approximate layout for Windows-less visual QA, not a Hancom render.
//
// usage: node render_rhwp.mjs <input.hwpx|input.hwp> <output-dir>
// stdout: one JSON object describing the rendered pages.

import fs from "node:fs";
import path from "node:path";
import url from "node:url";


const scriptDir = path.dirname(url.fileURLToPath(import.meta.url));


// rhwp uses its own metrics for known fonts and asks the host only for unknown
// faces. Same estimate as rhwp_convert.mjs: full-width Hangul/CJK, narrow Latin.
function estimateTextWidth(font, text) {
  const match = String(font ?? "").match(/([0-9.]+)px/);
  const size = match ? Number.parseFloat(match[1]) : 12;
  let width = 0;
  for (const character of String(text ?? "")) {
    const codepoint = character.codePointAt(0) ?? 0;
    width += codepoint >= 0x1100 && codepoint <= 0xffdc ? size : size * 0.55;
  }
  return width;
}


function svgSize(svg) {
  const head = svg.slice(0, 600);
  const width = Number.parseFloat((head.match(/\bwidth="([0-9.]+)"/) ?? [])[1]);
  const height = Number.parseFloat((head.match(/\bheight="([0-9.]+)"/) ?? [])[1]);
  return Number.isFinite(width) && Number.isFinite(height) ? { width, height } : null;
}


async function main() {
  const [inputPath, outputDir] = process.argv.slice(2);
  if (!inputPath || !outputDir) {
    throw new Error("usage: render_rhwp.mjs <input.hwpx|input.hwp> <output-dir>");
  }
  globalThis.measureTextWidth = estimateTextWidth;
  const rhwp = await import("./vendor/rhwp/rhwp.js");
  const wasm = fs.readFileSync(path.join(scriptDir, "vendor", "rhwp", "rhwp_bg.wasm"));
  await rhwp.default({ module_or_path: wasm });

  const document = new rhwp.HwpDocument(new Uint8Array(fs.readFileSync(inputPath)));
  try {
    fs.mkdirSync(outputDir, { recursive: true });
    const pages = [];
    const count = document.pageCount();
    for (let index = 0; index < count; index += 1) {
      const svg = document.renderPageSvg(index);
      const name = `page-${String(index + 1).padStart(3, "0")}.svg`;
      fs.writeFileSync(path.join(outputDir, name), svg);
      pages.push({ page: index + 1, svg: name, size_px: svgSize(svg) });
    }
    const warnings = JSON.parse(document.getValidationWarnings());
    const kinds = {};
    for (const warning of warnings.warnings ?? []) {
      kinds[warning.kind] = (kinds[warning.kind] ?? 0) + 1;
    }
    process.stdout.write(JSON.stringify({
      rhwp_version: rhwp.version(),
      source_format: document.getSourceFormat(),
      page_count: count,
      pages,
      validation_warnings: { count: warnings.count ?? 0, kinds },
    }));
  } finally {
    document.free();
  }
}


try {
  await main();
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`rhwp render failed: ${message}\n`);
  process.exitCode = 1;
}
