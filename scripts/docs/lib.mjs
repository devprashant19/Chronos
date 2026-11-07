// lib.mjs - shared helpers for the readme-suite scripts. Node built-ins only, so the scripts run in any
// project without an install, and can be copied into a repo to keep its docs honest.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";

export const WORK_DIR = ".readme-suite";

const SKIP_DIRS = new Set(["node_modules", ".git", ".hg", ".svn", "vendor", "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".gradle", ".idea", ".vscode", WORK_DIR]);

/** Set by the operating system or the build tool: nothing a project's docs should have to explain. */
export const SYSTEM_ENV = new Set(["NODE_ENV", "PATH", "PATHEXT", "HOME", "USER", "USERNAME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "SHELL", "PWD", "CI", "DEV", "PROD", "MODE", "SSR", "BASE_URL", "PROGRAMFILES", "COMSPEC", "SYSTEMROOT", "TERM", "LANG", "LC_ALL", "HOSTNAME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "NEXT_RUNTIME", "VERCEL", "VERCEL_ENV", "VERCEL_URL"]);

/** Parses --key value and --flag. Bare words land in args._ */
export function parseArgs(defaults = {}) {
  const out = { ...defaults, _: [] };
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith("--")) {
      const k = a[i].slice(2);
      const next = a[i + 1];
      if (next !== undefined && !next.startsWith("--")) {
        out[k] = next;
        i++;
      } else out[k] = true;
    } else out._.push(a[i]);
  }
  return out;
}

export const posix = (p) => p.split(sep).join("/");
export const readText = (p) => {
  try {
    // A Windows checkout has CRLF, and a regex ending in $ does not match before a \r.
    return readFileSync(p, "utf8").replace(/^\uFEFF/, "").replace(/\r\n/g, "\n");
  } catch {
    return null;
  }
};
export const readJson = (p) => {
  const t = readText(p);
  if (t === null) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
};

/** Every file in the project, repo-relative with forward slashes: what git tracks or would track, or a walk. */
export function listFiles(root) {
  const g = spawnSync("git", ["ls-files", "-co", "--exclude-standard", "-z"], { cwd: root, encoding: "utf8", maxBuffer: 2 ** 28 });
  if (g.status === 0 && g.stdout) {
    return g.stdout.split("\0").filter(Boolean).filter((f) => !f.split("/").some((part) => part === WORK_DIR) && existsSync(join(root, f)));
  }
  const out = [];
  const walk = (d) => {
    let entries;
    try {
      entries = readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name)) walk(join(d, e.name));
      } else if (e.isFile()) out.push(posix(relative(root, join(d, e.name))));
    }
  };
  walk(root);
  return out;
}

/**
 * Whether a path exists with exactly this spelling. Windows and macOS forgive `docs/API.md` for
 * `docs/api.md`; GitHub does not, so a link can pass here and 404 for every reader.
 */
export function existsExact(root, rel) {
  const parts = posix(rel).split("/").filter((p) => p && p !== ".");
  let cur = root;
  for (const part of parts) {
    if (part === "..") {
      cur = dirname(cur);
      continue;
    }
    let names;
    try {
      names = readdirSync(cur);
    } catch {
      return false;
    }
    if (!names.includes(part)) return false;
    cur = join(cur, part);
  }
  return true;
}

/** Blanks fenced code, keeping line numbers, so prose checks do not read examples. */
export function stripFences(md) {
  let inFence = false;
  let marker = "";
  return md.split("\n").map((line) => {
    const m = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (m) {
      if (!inFence) {
        inFence = true;
        marker = m[1][0];
      } else if (m[1][0] === marker) inFence = false;
      return "";
    }
    return inFence ? "" : line;
  }).join("\n");
}

export const stripInlineCode = (s) => s.replace(/`[^`\n]*`/g, (m) => " ".repeat(m.length));

/** Fenced blocks: { lang, code, line }. */
export function fences(md) {
  const out = [];
  let cur = null;
  md.split("\n").forEach((line, i) => {
    const m = line.match(/^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*)/);
    if (m) {
      if (!cur) cur = { lang: m[2].toLowerCase(), code: [], line: i + 1, marker: m[1][0] };
      else if (m[1][0] === cur.marker) {
        out.push({ lang: cur.lang, code: cur.code.join("\n"), line: cur.line });
        cur = null;
      } else cur.code.push(line);
    } else if (cur) cur.code.push(line);
  });
  return out;
}

/** The anchor GitHub gives a heading, with -1, -2 for repeats handled by the caller. */
export function slug(heading) {
  return heading
    .trim()
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[`*_~]/g, (c) => (c === "_" ? "_" : ""))
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, "")
    .replace(/\s/g, "-");
}

export function headings(md) {
  const seen = new Map();
  const out = [];
  stripFences(md).split("\n").forEach((line, i) => {
    const m = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (!m) return;
    const base = slug(m[2]);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    out.push({ level: m[1].length, text: m[2], anchor: n ? `${base}-${n}` : base, line: i + 1 });
  });
  return out;
}

/** A Chromium to drive headless: $BROWSER, then the usual places. Null if there is none. */
export function findBrowser() {
  const env = process.env.BROWSER || process.env.REMOTION_BROWSER;
  const candidates = [
    env,
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
  ].filter(Boolean);
  return candidates.find((c) => existsSync(c)) ?? null;
}

export const isFile = (p) => {
  try {
    return statSync(p).isFile();
  } catch {
    return false;
  }
};

export { basename, dirname, join, relative, resolve };
