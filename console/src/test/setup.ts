/**
 * Test environment for the console.
 *
 * jsdom has no EventSource, no IntersectionObserver and no matchMedia, all of
 * which the console touches on mount. Each is stubbed here rather than in
 * individual tests, because a component that crashes on mount fails every test
 * for the same uninteresting reason.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

/** A minimal EventSource that tests can drive. */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];

  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  /** Pretend the server accepted the subscription. */
  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  /** Push one frame, exactly as the verifier's SSE stream would. */
  send(payload: unknown) {
    this.onmessage?.({ data: typeof payload === "string" ? payload : JSON.stringify(payload) });
  }

  fail() {
    this.onerror?.();
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  static reset() {
    FakeEventSource.instances = [];
  }

  static get last(): FakeEventSource | undefined {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}

vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

vi.stubGlobal(
  "matchMedia",
  (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
);

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
vi.stubGlobal("IntersectionObserver", NoopObserver);
vi.stubGlobal("ResizeObserver", NoopObserver);

// jsdom does not implement it, and the inspector calls it when a claim is
// selected. Assigned unconditionally: the `in` guard narrows to `never` under
// `strict`, which is a type error rather than a safeguard.
Element.prototype.scrollIntoView = () => {};
