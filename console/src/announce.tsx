/**
 * Say what a click just did, and show where.
 *
 * Every control on the page changes something, and several change it
 * somewhere other than under the pointer: a nav link moves you to a section,
 * Live and Demo swap the source of every panel, Check again rewrites a report
 * further down. `announce(message, sectionId?)` puts one plain sentence in a
 * notice at the foot of the screen and, given a section, scrolls to it and
 * rings it for a moment.
 *
 * The notice is a polite live region, so a screen reader hears the same
 * sentence. Outside a provider (a component rendered alone in a test) the
 * default does nothing, so no component has to know whether it is mounted in
 * the page.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export interface Announcer {
  announce: (message: string, sectionId?: string) => void;
}

const AnnounceContext = createContext<Announcer>({ announce: () => undefined });

export const useAnnounce = (): Announcer => useContext(AnnounceContext);

/** How long the ring stays on a section, and the notice on screen. */
export const RING_MS = 2600;
export const NOTICE_MS = 6000;

/** Scroll to a section and ring it. Returns false when there is no such section. */
export function highlightSection(id: string): boolean {
  const el = document.getElementById(id);
  if (!el) return false;
  el.scrollIntoView?.({ behavior: "smooth", block: "start" });
  // Restart the ring if the same section is chosen twice in a row.
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
  window.setTimeout(() => el.classList.remove("flash"), RING_MS);
  return true;
}

export function AnnounceProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<{ text: string; key: number } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const announce = useCallback((message: string, sectionId?: string) => {
    if (sectionId) highlightSection(sectionId);
    setNotice({ text: message, key: Date.now() });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setNotice(null), NOTICE_MS);
  }, []);

  return (
    <AnnounceContext.Provider value={{ announce }}>
      {children}
      {/* Always in the document, so the live region exists before its first message. */}
      <div className="notice" role="status" aria-live="polite">
        {notice ? (
          <p key={notice.key} onClick={() => setNotice(null)} title="Dismiss">
            {notice.text}
          </p>
        ) : null}
      </div>
    </AnnounceContext.Provider>
  );
}
