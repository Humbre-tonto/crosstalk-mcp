#!/usr/bin/env node
"use strict";

/**
 * Copies the Python edition's runtime files into npx/python/ so they ship inside the published
 * npm package. Runs automatically on `npm pack`/`npm publish` via the package's "prepack" hook.
 * The bundled copy is git-ignored; running from a repo clone reads ../python directly instead.
 */

const fs = require("fs");
const path = require("path");

const srcDir = path.join(__dirname, "..", "..", "python");
const outDir = path.join(__dirname, "..", "python");
const FILES = ["crosstalk_mcp.py", "ui.html", "requirements.txt"];

fs.mkdirSync(outDir, { recursive: true });

let copied = 0;
for (const f of FILES) {
  const src = path.join(srcDir, f);
  if (fs.existsSync(src)) {
    fs.copyFileSync(src, path.join(outDir, f));
    copied++;
  } else {
    console.warn(`bundle-python: WARNING missing ${src}`);
  }
}

if (!fs.existsSync(path.join(outDir, "crosstalk_mcp.py"))) {
  console.error("bundle-python: FATAL crosstalk_mcp.py was not bundled; aborting.");
  process.exit(1);
}

console.log(`bundle-python: copied ${copied} file(s) into ${path.relative(process.cwd(), outDir)}`);
