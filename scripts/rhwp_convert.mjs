#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import url from "node:url";


const scriptDir = path.dirname(url.fileURLToPath(import.meta.url));


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


async function main() {
  const args = process.argv.slice(2);
  const infoMode = args[0] === "--info";
  const layoutInfoMode = args[0] === "--layout-info";
  const stdoutMode = args[0] === "--stdout";
  const inspectionMode = infoMode || layoutInfoMode;
  const inputPath = inspectionMode || stdoutMode ? args[1] : args[0];
  const outputPath = inspectionMode || stdoutMode ? undefined : args[1];
  if (!inputPath || (!inspectionMode && !stdoutMode && !outputPath)) {
    throw new Error(
      "usage: rhwp_convert.mjs [--info|--layout-info|--stdout] "
        + "<input.hwp> [output.hwpx]",
    );
  }

  const source = fs.readFileSync(inputPath);
  if (source[0] === 0x50 && source[1] === 0x4b) {
    throw new Error("input is already a ZIP-based HWPX document");
  }

  globalThis.measureTextWidth = estimateTextWidth;
  const rhwp = await import("./vendor/rhwp/rhwp.js");
  const wasm = fs.readFileSync(
    path.join(scriptDir, "vendor", "rhwp", "rhwp_bg.wasm"),
  );
  await rhwp.default({ module_or_path: wasm });

  const document = new rhwp.HwpDocument(new Uint8Array(source));
  try {
    if (layoutInfoMode) {
      const pageDefs = [];
      for (let section = 0; section < document.getSectionCount(); section += 1) {
        pageDefs.push(JSON.parse(document.getPageDef(section)));
      }
      process.stdout.write(JSON.stringify(pageDefs));
      return;
    }
    if (infoMode) {
      const sectionCount = document.getSectionCount();
      let paragraphCount = 0;
      for (let section = 0; section < sectionCount; section += 1) {
        paragraphCount += document.getParagraphCount(section);
      }
      const documentInfo = JSON.parse(document.getDocumentInfo());
      const warnings = JSON.parse(document.getValidationWarnings());
      process.stdout.write([
        document.getSourceFormat(),
        String(documentInfo.version),
        String(sectionCount),
        String(documentInfo.pageCount),
        String(paragraphCount),
        String(warnings.count),
      ].join("\n"));
      return;
    }
    const exported = document.exportHwpx();
    if (stdoutMode) {
      process.stdout.write(Buffer.from(exported));
    } else {
      fs.writeFileSync(outputPath, exported);
    }
  } finally {
    document.free();
  }
}


try {
  await main();
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`rhwp conversion failed: ${message}\n`);
  process.exitCode = 1;
}
