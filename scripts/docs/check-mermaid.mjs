#!/usr/bin/env node
/**
 * check-mermaid.mjs - parses every mermaid block with the real parser, in a real browser.
 *
 * A diagram with a syntax error does not fail loudly. GitHub draws a red error box where the picture should
 * be, and you find out when a reader tells you. Nothing short of the real parser agrees with GitHub.
 *
 *   node check-mermaid.mjs [--root .] [--mermaid <mermaid.min.js>] [--require] [files...]
 *
 * Needs a Chromium (Edge, Chrome, or $BROWSER). Fetches mermaid once to a temp folder, or uses --mermaid.
 * Exit 0 clean, 1 on a bad diagram. If it cannot run at all it says "NOT CHECKED" and exits 0, or 2 with
 * --require, so that CI can insist.
 */
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { parseArgs, listFiles, readText, fences, findBrowser, join, resolve } from "./lib.mjs";

const args = parseArgs({ root: ".", version: "11.12.0" });
const root = resolve(args.root);
const notChecked = (why) => {
  console.log(`NOT CHECKED: ${why}`);
  process.exit(args.require ? 2 : 0);
};

const targets = args._.length ? args._ : listFiles(root).filter((f) => /\.mdx?$/i.test(f) && !/(^|\/)(node_modules|vendor|templates?)\//.test(f));
const blocks = [];
for (const f of targets) {
  for (const b of fences(readText(join(root, f)) ?? "")) if (b.lang === "mermaid") blocks.push({ file: f, line: b.line, code: b.code });
}
if (!blocks.length) {
  console.log("no mermaid blocks");
  process.exit(0);
}

const browser = findBrowser();
if (!browser) notChecked("no Chromium found. Set BROWSER to one");

let lib = args.mermaid ? resolve(args.mermaid) : null;
if (!lib) {
  const cache = join(tmpdir(), "readme-suite-cache");
  mkdirSync(cache, { recursive: true });
  lib = join(cache, `mermaid-${args.version}.min.js`);
  if (!existsSync(lib)) {
    try {
      const r = await fetch(`https://cdn.jsdelivr.net/npm/mermaid@${args.version}/dist/mermaid.min.js`, { signal: AbortSignal.timeout(60000) });
      if (!r.ok) throw new Error(String(r.status));
      writeFileSync(lib, Buffer.from(await r.arrayBuffer()));
    } catch (e) {
      notChecked(`could not fetch mermaid (${e.message}). Pass --mermaid <mermaid.min.js>`);
    }
  }
}

const dir = mkdtempSync(join(tmpdir(), "readme-suite-mermaid-"));
const page = join(dir, "check.html");
// Everything runs in the page and the answer is read back from the DOM, so this needs no driver library.
writeFileSync(page, `<!doctype html><meta charset="utf-8"><pre id="out">PENDING</pre>
<script src="${pathToFileURL(lib).href}"></script>
<script>
const blocks = ${JSON.stringify(blocks.map((b) => b.code)).replace(/</g, "\\u003c")};
(async () => {
  mermaid.initialize({ startOnLoad: false });
  const results = [];
  for (const code of blocks) {
    try { await mermaid.parse(code); results.push(null); }
    catch (e) { results.push(String(e.message || e)); }
  }
  document.getElementById("out").textContent = "RESULT:" + encodeURIComponent(JSON.stringify(results));
})();
</script>`);
const run = spawnSync(browser, ["--headless=new", "--disable-gpu", "--no-first-run", `--user-data-dir=${join(dir, "profile")}`, "--allow-file-access-from-files", "--virtual-time-budget=30000", "--dump-dom", pathToFileURL(page).href], { encoding: "utf8", maxBuffer: 2 ** 28, timeout: 120000 });
const found = (run.stdout ?? "").match(/RESULT:([^<\s]+)/);
try {
  rmSync(dir, { recursive: true, force: true });
} catch { /* a browser can hold its profile for a moment; it is only a temp folder */ }
if (!found) notChecked(`the browser gave no answer (${run.error?.message ?? `exit ${run.status}`})`);

const results = JSON.parse(decodeURIComponent(found[1]));
let bad = 0;
results.forEach((err, i) => {
  if (!err) return;
  bad++;
  console.log(`  x ${blocks[i].file}:${blocks[i].line}\n    ${err.split("\n").slice(0, 4).join("\n    ")}`);
});
console.log(`${blocks.length} mermaid block(s), ${bad} broken (mermaid ${args.mermaid ? "from file" : args.version})`);
process.exit(bad ? 1 : 0);
