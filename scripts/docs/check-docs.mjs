#!/usr/bin/env node
/**
 * check-docs.mjs - the gate. A document that claims something false is worse than no document, because
 * people act on it. This catches what a reader would otherwise find for you: a link that 404s, a command
 * that does not exist, a number that says one thing here and another there.
 *
 *   node check-docs.mjs [--root .] [--strict] [--json]
 *
 * Exit 0 clean, 1 on any failure (with --strict, any warning too). Node built-ins only, so it can live in a
 * repo and run in CI. To accept one line, end it with <!-- check-docs: ignore -->, and say why nearby.
 *
 *   structure  the plan's documents exist; no placeholder left; README has its parts; one H1
 *   links      relative links and anchors resolve, with the exact spelling GitHub needs; images exist and
 *              have alt text
 *   commands   every npm/pnpm/yarn/bun/make/just command named in the docs exists
 *   paths      every repo path in backticks exists
 *   env        variables documented exist in the code; variables the code reads are documented
 *   numbers    the same claim does not carry two numbers; known facts match the repo
 *   voice      no marketing language
 *   leaks      no local paths or secrets in any markdown, linked or not
 *   reach      every markdown file can be reached from the README; docs/README.md indexes docs/
 *
 * Only pages a reader can reach are held to all of this. Strays are reported, and checked for leaks.
 *   mermaid    a static lint. check-mermaid.mjs runs the real parser
 */
import { statSync } from "node:fs";
import { parseArgs, listFiles, readText, readJson, existsExact, stripFences, stripInlineCode, fences, headings, join, resolve, dirname, posix, WORK_DIR, SYSTEM_ENV } from "./lib.mjs";
import { relative } from "node:path";
import { spawnSync } from "node:child_process";

const args = parseArgs({ root: "." });
const root = resolve(args.root);
const files = listFiles(root);
const results = [];
const IGNORE = /<!--\s*check-docs:\s*ignore\s*-->/;
const add = (level, check, file, line, msg) => results.push({ level, check, file, line, msg });
const fail = (check, file, line, msg) => add("fail", check, file, line, msg);
const warn = (check, file, line, msg) => add("warn", check, file, line, msg);

const VENDORED = /(^|\/)(\.[^/]+|node_modules|vendor|third_party|fixtures?|testdata|examples?|templates?|__snapshots__)\//;
const docs = files.filter((f) => /\.mdx?$/i.test(f) && !VENDORED.test(f));
const text = Object.fromEntries(docs.map((f) => [f, readText(join(root, f)) ?? ""]));
const linesOf = (f) => text[f].split("\n");
const ignored = (f, n) => IGNORE.test(linesOf(f)[n - 1] ?? "");
const plan = readJson(join(root, WORK_DIR, "plan.json"));
const README = "README.md";
const pkg = readJson(join(root, "package.json"));

// What a reader can get to from the README, plus what is found by name: agents read their files, GitHub
// surfaces the community ones, and a package in a monorepo has its own README. These are held to the
// whole standard. Anything else is a stray: it is reported, and checked for leaks, and not proofread.
const reached = new Set();
if (text[README]) {
  reached.add(README);
  const queue = [README];
  const visit = (rel) => {
    if (text[rel] !== undefined && !reached.has(rel)) {
      reached.add(rel);
      queue.push(rel);
    }
  };
  while (queue.length) {
    const f = queue.pop();
    const prose = stripFences(text[f] ?? "");
    for (const m of prose.matchAll(/\]\(\s*<?([^)\s>#]+)/g)) {
      if (/^[a-z][a-z0-9+.-]*:/i.test(m[1])) continue;
      const rel = posix(relative(root, resolve(root, dirname(f), m[1])));
      visit(/\.mdx?$/i.test(rel) ? rel : `${rel}/README.md`);
    }
    for (const m of prose.matchAll(/`([\w./-]+\.md)`/g)) visit(posix(relative(root, resolve(root, dirname(f), m[1]))));
  }
}
const foundByName = (f) => /^(CLAUDE|AGENTS|GEMINI|CHANGELOG|CONTRIBUTING|SECURITY|CODE_OF_CONDUCT|SUPPORT)\.md$/i.test(f) || /\/README\.md$/i.test(f);
const planned = new Set((plan?.docs ?? []).map((d) => d.path));
const core = text[README] ? docs.filter((f) => reached.has(f) || foundByName(f) || planned.has(f)) : docs;

// ---------------------------------------------------------------- structure
const placeholder = /\{\{\s*(FILL\b[^}]*|[A-Z_]{3,})\s*\}\}/;
for (const f of core) {
  linesOf(f).forEach((line, i) => {
    if (placeholder.test(line) && !ignored(f, i + 1)) fail("structure", f, i + 1, `placeholder left: ${line.match(placeholder)[0].slice(0, 70)}`);
  });
  const h = headings(text[f]);
  const h1 = h.filter((x) => x.level === 1);
  if (h1.length > 1) warn("structure", f, h1[1].line, `${h1.length} top-level headings; a page has one`);
  for (let i = 1; i < h.length; i++) if (h[i].level > h[i - 1].level + 1) warn("structure", f, h[i].line, `heading jumps from level ${h[i - 1].level} to ${h[i].level}`);
}
for (const f of files.filter((x) => /^docs\/.+\.html?$/i.test(x))) {
  (readText(join(root, f)) ?? "").split("\n").forEach((line, i) => {
    if (placeholder.test(line)) fail("structure", f, i + 1, `placeholder left: ${line.match(placeholder)[0].slice(0, 70)}`);
  });
}
if (plan) {
  for (const d of plan.docs ?? []) if (!existsExact(root, d.path)) fail("structure", d.path, 0, `the plan requires this document, and it does not exist. Write it, or move it to "skipped" with a reason`);
  for (const d of plan.skipped ?? []) if (!d.why || d.why.trim().length < 3) fail("structure", d.path, 0, "skipped with no reason");
}
if (!text[README]) fail("structure", README, 0, "no README.md");
else {
  const md = text[README];
  const h = headings(md);
  const prose = stripFences(md);
  const hasH = (re) => h.some((x) => re.test(x.text));
  if (!h.length || h[0].level !== 1) fail("structure", README, 1, "starts without a title");
  const firstH2 = h.find((x) => x.level === 2)?.line ?? md.split("\n").length;
  const opening = prose.split("\n").slice((h[0]?.line ?? 0), firstH2 - 1).join(" ").replace(/[*_#>|-]/g, " ").trim();
  if (opening.split(/\s+/).length < 25) fail("structure", README, 1, "the opening is under 25 words: say what problem this solves before the first section");
  if (!hasH(/\b(run|install|quick ?start|getting started|setup|usage|use it|try it)\b/i)) fail("structure", README, 0, "no section that says how to run it");
  if (!hasH(/verif|tested|what works|status|limits/i)) fail("structure", README, 0, 'no "What is verified, and what is not" section');
  else if (!/not verified|not tested|unverified|known limits|does not work|not yet/i.test(prose)) fail("structure", README, 0, "says what is verified and nothing about what is not. Every project has limits: name them");
  if (!/licen[cs]e/i.test(prose)) warn("structure", README, 0, "no licence mentioned");
  const n = md.split("\n").length;
  if (n > 500) warn("structure", README, 0, `${n} lines. Past about 400, move detail into docs/ and link to it`);
  const earlyLinks = md.split("\n").slice(0, Math.min(45, n)).join("\n");
  if (docs.some((f) => f.startsWith("docs/")) && !/\]\((\.\/)?docs\/|\]\(ARCHITECTURE/i.test(earlyLinks)) warn("structure", README, 0, "the map of the docs comes late. Readers decide in the first screen: link to them near the top");
  if (n > 200 && (md.match(/\]\(#[^)]+\)/g) ?? []).length < 3) warn("structure", README, 0, `${n} lines and no contents. Add a jump list after the opening`);
}

// ---------------------------------------------------------------- links
const anchorsCache = {};
const anchorsOf = (f) => (anchorsCache[f] ??= new Set(headings(readText(join(root, f)) ?? "").map((h) => h.anchor)));
for (const f of core) {
  const body = stripFences(text[f]).split("\n");
  body.forEach((raw, i) => {
    if (ignored(f, i + 1)) return;
    const line = stripInlineCode(raw);
    for (const m of line.matchAll(/(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g)) {
      const [, bang, label, target] = m;
      if (bang && !label.trim()) fail("links", f, i + 1, `image with no alt text: ${target}`);
      if (/^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
      const [pathPart, anchor] = target.split("#");
      const rel = pathPart ? posix(relative(root, resolve(root, dirname(f), decodeURI(pathPart)))) : f;
      if (rel.startsWith("..")) {
        warn("links", f, i + 1, `points outside the repo: ${target}`);
        continue;
      }
      if (pathPart && !existsExact(root, rel)) {
        const near = files.find((x) => x.toLowerCase() === rel.toLowerCase());
        fail("links", f, i + 1, near ? `wrong case: ${target} is ${near} (works on Windows and Mac, 404s on GitHub)` : `broken ${bang ? "image" : "link"}: ${target}`);
        continue;
      }
      if (anchor && /\.mdx?$/i.test(rel) && !anchorsOf(rel).has(decodeURI(anchor).toLowerCase())) fail("links", f, i + 1, `no such heading: ${target}`);
      if (bang && pathPart) {
        try {
          const size = statSync(join(root, rel)).size;
          if (size > 3 * 1024 * 1024) warn("links", f, i + 1, `image is ${(size / 1048576).toFixed(1)} MB: ${target}`);
        } catch { /* reported above */ }
      }
    }
  });
}

// ---------------------------------------------------------------- commands
const manifests = files.filter((x) => /(^|\/)package\.json$/.test(x) && !VENDORED.test(x)).map((x) => readJson(join(root, x))).filter(Boolean);
const npmScripts = new Set(manifests.flatMap((m) => Object.keys(m.scripts ?? {})));
const anyNpm = manifests.length > 0;
const npmDeps = new Set(manifests.flatMap((m) => [...Object.keys(m.dependencies ?? {}), ...Object.keys(m.devDependencies ?? {})]));
const KNOWN_BINS = new Set(["tsc", "tsx", "vitest", "jest", "eslint", "prettier", "vite", "next", "playwright", "drizzle-kit", "prisma", "turbo", "nx", "wrangler", "vercel", "hardhat", "biome", "oxlint", "knip", "tailwindcss", "nodemon", "ts-node", "remotion", "storybook", "expo"]);
const makefiles = files.filter((x) => /(^|\/)(GNUmakefile|Makefile|makefile)$/.test(x));
const makeTargets = new Set(makefiles.flatMap((x) => [...(readText(join(root, x)) ?? "").matchAll(/^([a-zA-Z][\w.-]*)\s*:/gm)].map((m) => m[1])));
const justTargets = new Set(files.filter((x) => /(^|\/)[Jj]ustfile$/.test(x)).flatMap((x) => [...(readText(join(root, x)) ?? "").matchAll(/^@?([a-zA-Z][\w-]*)[^:\n=]*:/gm)].map((m) => m[1])));
const BUILTIN_NPM = new Set(["install", "i", "ci", "init", "publish", "pack", "link", "view", "update", "audit", "exec", "login", "run", "add", "remove", "create", "dlx", "why", "list", "ls", "version", "outdated", "uninstall", "cache", "config", "x", "global", "up", "upgrade", "import", "store", "patch", "rebuild", "prune", "fund", "doctor", "help"]);
const checkCommands = (f, n, line) => {
  if (ignored(f, n)) return;
  const clean = line.replace(/#.*$/, "");
  for (const m of clean.matchAll(/(?:^|[\s`;&|(])(npm|pnpm|yarn|bun)\s+(run\s+)?([a-zA-Z][\w:.-]*)/g)) {
    const [, tool, run, name] = m;
    if (!anyNpm) continue;
    if (!run && BUILTIN_NPM.has(name)) continue;
    if (!run && tool === "npm" && !["test", "t", "start", "stop", "restart"].includes(name)) continue;
    const want = name === "t" ? "test" : name;
    if (!run && tool !== "npm" && (npmDeps.has(want) || KNOWN_BINS.has(want))) continue; // `pnpm vitest` runs a binary
    if (!npmScripts.has(want)) fail("commands", f, n, `\`${tool} ${run ?? ""}${name}\` is not a script in any package.json`);
  }
  if (makefiles.length) for (const m of clean.matchAll(/(?:^|[\s`;&|(])make\s+([a-zA-Z][\w.-]*)/g)) if (!makeTargets.has(m[1])) fail("commands", f, n, `\`make ${m[1]}\` is not a target`);
  if (justTargets.size) for (const m of clean.matchAll(/(?:^|[\s`;&|(])just\s+([a-zA-Z][\w-]*)/g)) if (!justTargets.has(m[1])) fail("commands", f, n, `\`just ${m[1]}\` is not a recipe`);
};
const SHELL = new Set(["", "bash", "sh", "shell", "console", "zsh", "powershell", "ps1", "cmd", "bat", "text"]);
for (const f of core) {
  const prose = stripFences(text[f]).split("\n");
  prose.forEach((line, i) => {
    for (const m of line.matchAll(/`([^`\n]+)`/g)) checkCommands(f, i + 1, m[1]);
  });
  for (const b of fences(text[f])) {
    if (!SHELL.has(b.lang)) continue;
    b.code.split("\n").forEach((line, i) => checkCommands(f, b.line + 1 + i, line));
  }
}

// ---------------------------------------------------------------- paths
const rootEntries = new Set(files.map((x) => x.split("/")[0]));
// A path git ignores is one the reader creates or generates: it should not exist in a clean clone.
const gitIgnores = (x) => spawnSync("git", ["check-ignore", "-q", x], { cwd: root }).status === 0;
for (const f of core) {
  stripFences(text[f]).split("\n").forEach((raw, i) => {
    if (ignored(f, i + 1)) return;
    // Link text that points somewhere else is about that place, not this repo. The link check has the rest.
    const line = raw.replace(/\[([^\]]*)\]\([^)]*\)/g, " ");
    for (const m of line.matchAll(/`([^`\n]+)`/g)) {
      let p = m[1].replace(/:\d+(-\d+)?$/, "").replace(/^\.\//, "").replace(/\/$/, "");
      if (/[\s<>*{}$|=,;()"'\\\[\]]/.test(p) || /^[a-z]+:/i.test(p) || /^[~/.]{1,2}\//.test(m[1]) && !m[1].startsWith("./") && !m[1].startsWith("../") || p.startsWith("-") || p.startsWith("@")) continue;
      if (p.includes("/")) {
        if (!rootEntries.has(p.split("/")[0])) continue; // a route, a package name, a path elsewhere
        const dirExists = files.some((x) => x === p || x.startsWith(`${p}/`));
        if (!dirExists && !existsExact(root, p) && !/(^|\/)\.env$/.test(p) && !gitIgnores(p)) fail("paths", f, i + 1, `\`${m[1]}\` does not exist`);
      } else if (/^[A-Za-z0-9._-]+\.(md|json|ya?ml|toml|lock)$/.test(p) && /^[A-Z]/.test(p) && !files.some((x) => x === p || x.endsWith(`/${p}`)) && !gitIgnores(p)) {
        fail("paths", f, i + 1, `\`${p}\` does not exist`);
      }
    }
  });
}

// ---------------------------------------------------------------- env
const source = files.filter((x) => /\.(m?[jt]sx?|cjs|py|rs|go|rb|java|kt)$/.test(x) && !VENDORED.test(x) && !/(^|\/)(scripts?|tools?|tests?|__tests__)\//.test(x));
const ENV_RES = [/process\.env\.([A-Z][A-Z0-9_]+)/g, /process\.env\[["'`]([A-Z][A-Z0-9_]+)["'`]\]/g, /import\.meta\.env\.([A-Z][A-Z0-9_]+)/g, /os\.environ(?:\.get)?[\[(]\s*["']([A-Z][A-Z0-9_]+)["']/g, /os\.getenv\(\s*["']([A-Z][A-Z0-9_]+)["']/g, /env::var\(\s*"([A-Z][A-Z0-9_]+)"/g, /os\.Getenv\(\s*"([A-Z][A-Z0-9_]+)"/g];
const codeEnv = new Map();
let allCode = "";
for (const x of source) {
  const t = readText(join(root, x));
  if (!t || t.length > 2_000_000) continue;
  allCode += t;
  for (const re of ENV_RES) for (const m of t.matchAll(re)) if (!codeEnv.has(m[1]) && !SYSTEM_ENV.has(m[1])) codeEnv.set(m[1], x);
}
const docsAll = docs.map((f) => text[f]).join("\n");
const exampleFile = files.find((x) => /(^|\/)\.env\.(example|sample|template)$/.test(x));
const example = exampleFile ? readText(join(root, exampleFile)) ?? "" : "";
const undocumented = [...codeEnv.keys()].filter((v) => !new RegExp(`\\b${v}\\b`).test(docsAll) && !new RegExp(`\\b${v}\\b`).test(example));
if (undocumented.length) fail("env", exampleFile ?? README, 0, `the code reads ${undocumented.length} variable(s) documented nowhere: ${undocumented.slice(0, 12).join(", ")}${undocumented.length > 12 ? ", ..." : ""}`);
for (const f of docs.filter((x) => /environment|configuration/i.test(x) || x === README)) {
  stripFences(text[f]).split("\n").forEach((line, i) => {
    if (!/^\s*\|/.test(line) || ignored(f, i + 1)) return;
    const first = line.split("|")[1] ?? "";
    for (const m of first.matchAll(/`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`/g)) {
      const v = m[1];
      if (!new RegExp(`\\b${v}\\b`).test(allCode) && !new RegExp(`\\b${v}\\b`).test(example)) warn("env", f, i + 1, `\`${v}\` is documented, and nothing in the code or the example file mentions it`);
    }
  });
}

// ---------------------------------------------------------------- numbers
const UNITS = /^(second|seconds|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years|ms|s|kb|mb|gb|tb|px|pixels|percent|times|x|per|of|or|and|to|in|at|a|an|the|by|from|with|on|for|is|are|was|were|more|less|than|fewer|other|words?|characters?|lines?|bytes?|bits?|steps?|levels?|ways?|things?|parts?|kinds?|types?|people|users?|columns?|rows?|widths?|takes?)$/i;
const SYNONYMS = [[/\b(regular expressions?|regex(es)?|gate patterns?)\b/gi, "regexes"], [/\bunit (tests?|checks?)\b/gi, "unit tests"], [/\be2e (tests?|checks?)\b/gi, "end-to-end tests"], [/\b(rest routes?|rest endpoints?|express endpoints?|api routes?|api endpoints?|http endpoints?)\b/gi, "routes"]];
const claims = new Map();
const note = (key, value, f, line) => {
  if (!claims.has(key)) claims.set(key, new Map());
  const m = claims.get(key);
  if (!m.has(value)) m.set(value, []);
  m.get(value).push(`${f}:${line}`);
};
for (const f of core) {
  const fenced = text[f].split("\n");
  stripFences(text[f]).split("\n").forEach((raw, i) => {
    const original = fenced[i] ?? raw;
    if (IGNORE.test(original)) return;
    let line = raw.replace(/\*\*/g, "");
    for (const [re, to] of SYNONYMS) line = line.replace(re, to);
    const code = [...raw.matchAll(/`([^`]+)`/g)].map((m) => m[1]).find((c) => /^(npm|pnpm|yarn|bun|make|just|cargo|pytest|go)\b/.test(c));
    const prose = stripInlineCode(line);
    for (const m of prose.matchAll(/(?<![\d.,:/-])(\d[\d,]*)\s+([a-z][a-z-]+)(?:\s+([a-z][a-z-]+))?/g)) {
      const value = m[1].replace(/,/g, "");
      const [w1, w2] = [m[2].toLowerCase(), m[3]?.toLowerCase()];
      if (UNITS.test(w1)) continue;
      if (w2 && w1 === "unit" && /^tests$/.test(w2)) note("unit tests", value, f, i + 1);
      else if (["routes", "regexes"].includes(w1)) note(w1, value, f, i + 1);
      else if (/^(checks?|tests?)$/.test(w1) && code) note(`${w1.replace(/s$/, "")}s in \`${code}\``, value, f, i + 1);
      else if (w2 && /s$/.test(w2) && !UNITS.test(w2) && /^(source|test|database|api|rest|react|ui|config|migration|cli|agent)$/.test(w1)) note(`${w1} ${w2}`, value, f, i + 1);
    }
    for (const m of prose.matchAll(/\b(Node(?:\.js)?|Python)\s+(\d+(?:\.\d+)?)(?:\+|\s+or\s+(?:newer|later|above|higher))/g)) note(`minimum ${m[1].replace(".js", "")} version`, m[2], f, i + 1);
  });
}
// The comment beside a command in a code block makes the same claims, and is the easiest place to forget.
for (const f of core) {
  for (const b of fences(text[f]).filter((x) => SHELL.has(x.lang))) {
    b.code.split("\n").forEach((raw, i) => {
      const [, cmd, comment] = raw.match(/^\s*([^#\s][^#]*?)\s+#\s*(.+)$/) ?? [];
      if (!comment || IGNORE.test(raw)) return;
      let line = comment;
      for (const [re, to] of SYNONYMS) line = line.replace(re, to);
      for (const m of line.matchAll(/(?<![\d.,:/-])(\d[\d,]*)\s+([a-z][a-z-]+)(?:\s+([a-z][a-z-]+))?/g)) {
        const value = m[1].replace(/,/g, "");
        const [w1, w2] = [m[2].toLowerCase(), m[3]?.toLowerCase()];
        if (w1 === "unit" && w2 === "tests") note("unit tests", value, f, b.line + 1 + i);
        else if (/^(checks?|tests?)$/.test(w1)) note(`${w1.replace(/s$/, "")}s in \`${cmd.trim()}\``, value, f, b.line + 1 + i);
      }
    });
  }
}
for (const [key, values] of claims) {
  if (values.size < 2) continue;
  const parts = [...values].map(([v, where]) => `${v} (${where.slice(0, 2).join(", ")}${where.length > 2 ? ` +${where.length - 2}` : ""})`);
  const first = [...values.values()][0][0].split(":");
  fail("numbers", first[0], Number(first[1]), `"${key}" has ${values.size} different values: ${parts.join(" vs ")}. Keep it in one place and link there`);
}
// Known facts.
const engineNode = pkg?.engines?.node?.match(/\d+/)?.[0];
if (engineNode && claims.has("minimum Node version")) for (const [v, where] of claims.get("minimum Node version")) if (Number(v) !== Number(engineNode)) warn("numbers", where[0].split(":")[0], Number(where[0].split(":")[1]), `says Node ${v}, and package.json engines says ${pkg.engines.node}`);
const srcCount = files.filter((x) => /\.(m?[jt]sx?|cjs|vue|svelte|py|rs|go|rb|java|kt|swift|dart|php|cs|cpp|cc|c|h|sol)$/.test(x) && !VENDORED.test(x) && !/(^|\/)(tests?|__tests__|scripts|tools)\/|\.(test|spec)\.|\.config\./.test(x)).length;
if (claims.has("source files")) for (const [v, where] of claims.get("source files")) if (Math.abs(Number(v) - srcCount) > Math.max(3, srcCount * 0.1)) fail("numbers", where[0].split(":")[0], Number(where[0].split(":")[1]), `says ${v} source files, and the repo has about ${srcCount}. Numbers like this go stale: remove it, or update it`);
const licenceFile = files.find((x) => /^(licen[cs]e|copying)(\.[a-z]+)?$/i.test(x));
// Saying there is none yet is the honest answer. Naming one with no file behind it is the fault.
const LICENCE_NAME = /##\s*licen[cs]e[\s\S]*?\b(MIT|Apache|GPL|AGPL|LGPL|BSD|MPL)\b/i;
if (text[README] && LICENCE_NAME.test(text[README]) && !licenceFile) fail("structure", README, 0, "the README names a licence, and there is no LICENSE file: without one, nobody may legally use this");
if (licenceFile && text[README]) {
  const lt = readText(join(root, licenceFile)) ?? "";
  const says = text[README].match(LICENCE_NAME)?.[1]?.toUpperCase();
  if (says && !new RegExp(says, "i").test(lt) && !(says === "MPL" && /Mozilla/i.test(lt)) && !(says === "GPL" && /GENERAL PUBLIC/i.test(lt))) fail("structure", README, 0, `says ${says}, and ${licenceFile} is something else`);
}

// ---------------------------------------------------------------- voice
const BANNED = /\b(seamless(ly)?|robust|cutting[- ]edge|state[- ]of[- ]the[- ]art|blazing(ly)?|lightning[- ]fast|world[- ]class|best[- ]in[- ]class|next[- ]gen(eration)?|game[- ]chang(er|ing)|revolutionary|supercharge[sd]?|effortless(ly)?|comprehensive|enterprise[- ]grade|unleash|empower(s|ing)?|delve|harness(es|ing)?|elevate[sd]?|we have thoroughly|powerful|leverag(e|es|ing)|in today's|easy to use|simply)\b/i;
for (const f of core) {
  stripFences(text[f]).split("\n").forEach((raw, i) => {
    if (ignored(f, i + 1)) return;
    const line = stripInlineCode(raw).replace(/\]\([^)]*\)/g, "]");
    const m = line.match(BANNED);
    if (m) fail("voice", f, i + 1, `"${m[0]}" tells the reader to believe something. Say what it does, or give the number`);
    if (/^#{1,6}\s.*[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(line)) warn("voice", f, i + 1, "emoji in a heading: it also changes the anchor");
  });
}

// ---------------------------------------------------------------- leaks: every markdown file, including the ones nobody links to
const SECRET = /\b(sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|hf_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)/;
const LOCAL = /(?:\b[A-Z]:[\\/]Users[\\/](?!you\b|your|name\b|username\b|user\b|<)[^\s\\/`"')]+|\/(?:home|Users)\/(?!you\b|your|name\b|username\b|user\b|runner\b|<|\$)[a-z0-9_.-]+\/)/i;
for (const f of docs) {
  linesOf(f).forEach((line, i) => {
    if (ignored(f, i + 1)) return;
    const s = line.match(SECRET);
    if (s) fail("leaks", f, i + 1, `looks like a secret: ${s[0].slice(0, 8)}...`);
    const l = line.match(LOCAL);
    if (l) fail("leaks", f, i + 1, `a path from one person's machine: ${l[0]}`);
  });
}

// ---------------------------------------------------------------- reach
if (text[README]) for (const f of docs) if (!core.includes(f)) warn("reach", f, 0, "no document links here. Link it, or if it is a stray working note, take it out of the repo");
if (text["docs/README.md"]) {
  const index = text["docs/README.md"];
  for (const f of docs.filter((x) => /^docs\/[^/]+\.mdx?$/.test(x) && x !== "docs/README.md")) {
    const name = f.slice(5);
    if (!new RegExp(`\\]\\((\\./)?${name.replace(/[.]/g, "\\.")}`).test(index)) fail("reach", "docs/README.md", 0, `does not list ${name}`);
  }
}

// ---------------------------------------------------------------- mermaid, statically
const MERMAID_TYPES = /^\s*(%%\{.*\}%%\s*)?(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|quadrantChart|requirementDiagram|C4Context|C4Container|C4Component|sankey-beta|xychart-beta|block-beta|architecture-beta|kanban|packet-beta)\b/;
for (const f of core) {
  for (const b of fences(text[f]).filter((x) => x.lang === "mermaid")) {
    if (!MERMAID_TYPES.test(b.code.replace(/^\s*\n/, ""))) fail("mermaid", f, b.line, "does not start with a known diagram type");
    if (/^(graph|flowchart)/m.test(b.code)) {
      b.code.split("\n").forEach((line, i) => {
        if (/\[[^\]"]*\([^\]"]*\][^"]*$/.test(line) || /\{[^}"]*\([^}"]*\}/.test(line)) fail("mermaid", f, b.line + 1 + i, `unquoted parentheses in a label break the diagram: ${line.trim().slice(0, 60)}`);
        if (/(^|\s)end\s*(\[|\(|-->)/.test(line)) fail("mermaid", f, b.line + 1 + i, 'a node called "end" breaks the diagram');
      });
    }
  }
}

// ---------------------------------------------------------------- report
const order = ["structure", "links", "commands", "paths", "env", "numbers", "voice", "leaks", "reach", "mermaid"];
results.sort((a, b) => (a.level === b.level ? order.indexOf(a.check) - order.indexOf(b.check) : a.level === "fail" ? -1 : 1));
const fails = results.filter((r) => r.level === "fail");
const warns = results.filter((r) => r.level === "warn");
if (args.json) console.log(JSON.stringify({ docs: docs.length, fails, warns }, null, 2));
else {
  console.log(`checked ${docs.length} markdown file(s) in ${root}`);
  for (const r of results) console.log(`  ${r.level === "fail" ? "x" : "-"} [${r.check}] ${r.file}${r.line ? `:${r.line}` : ""}  ${r.msg}`);
  console.log(`\n${fails.length} failure(s), ${warns.length} warning(s).${fails.length ? " Fix the docs or the code, never the checker." : ""}`);
}
process.exit(fails.length || (args.strict && warns.length) ? 1 : 0);
