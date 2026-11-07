/**
 * Reveal each section as the reader reaches it.
 *
 * An IntersectionObserver rather than a scroll handler: the browser does the
 * work off the main thread, and a long page of panels does not cost a layout
 * read on every frame.
 *
 * Under prefers-reduced-motion nothing is hidden in the first place -- the
 * class is simply never applied, so the page renders complete.
 */
import { useEffect } from "react";

export function useReveal(): void {
  useEffect(() => {
    let reduced = false;
    try {
      reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
    } catch {
      reduced = false;
    }
    if (reduced) return;

    const targets = Array.from(
      document.querySelectorAll<HTMLElement>("main section:not(.hero)"),
    );
    if (!targets.length || typeof IntersectionObserver === "undefined") return;

    targets.forEach((el) => el.classList.add("reveal-on-scroll"));

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("seen");
            io.unobserve(e.target);
          }
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.06 },
    );
    targets.forEach((el) => io.observe(el));

    return () => {
      io.disconnect();
      // Leave nothing hidden if this unmounts mid-scroll.
      targets.forEach((el) => el.classList.remove("reveal-on-scroll"));
    };
  }, []);
}
