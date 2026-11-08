/**
 * The page's sections: anchor, nav label, and one sentence on what to look at.
 *
 * Shared by the header nav and by every link that jumps to a section, so the
 * sentence a reader is shown when they arrive is the same wherever they came
 * from. `App.test.tsx` checks every id here is a section on the page.
 */

export const SECTIONS = [
  ["path", "Signal path", "The eight steps one request takes through the gateway, coloured by who owns each. Press a step to hold it."],
  ["run", "Run it", "Press Start, then keep pressing: one question is asked, cached, checked, found stale and deleted, five steps in order."],
  ["traffic", "Traffic", "Every verification as it finishes. Blue rows were served from the cache, and those are the ones that can be stale."],
  ["inspect", "Inspector", "One answer with its time-sensitive claims lit up. Select a claim to see every term of its score and the pages behind it."],
  ["cache", "Cache", "The cache lifetime Chronos chose for each kind of fact, against one flat default, and every stale entry it deleted."],
  ["playground", "Playground", "The same question sent twice through the gateway, with the raw headers your browser received."],
  ["bench", "Benchmark", "Precision, recall and cost, measured against 120 hand-labelled claims and read from the results file."],
  ["brief", "Briefing", "A meeting brief built on the same checks: what is confirmed, what is not, and what to ask."],
  ["proof", "Every feature", "Each feature run against the live stack, with the headers and counters that came back."],
] as const;

export type SectionId = (typeof SECTIONS)[number][0];

/** The nav label and sentence for a section id, or null for an unknown one. */
export function sectionInfo(id: string): { label: string; what: string } | null {
  const found = SECTIONS.find(([sid]) => sid === id.replace(/^#/, ""));
  return found ? { label: found[1], what: found[2] } : null;
}

/** The sentence shown when a reader is taken to a section. */
export function arrivalMessage(id: string): string {
  const info = sectionInfo(id);
  return info ? `${info.label}: ${info.what}` : "";
}
