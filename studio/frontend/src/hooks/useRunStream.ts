// Live event stream for one run. Wraps the browser EventSource, closes on
// terminal events, and falls back gracefully if the run already finished.
//
// We deliberately keep the state surface tiny: a flat list of events in
// arrival order, plus a map of "latest status per case" for the case grid.
// The full delta/trace payloads are NOT in the stream; the run detail page
// fetches them via /api/runs/{id}/cases when the run finishes.

import { useEffect, useRef, useState } from "react";

export type RunEvent = {
  kind: string;
  level?: string;
  message?: string;
  payload?: Record<string, unknown> | null;
};

export type CaseStatus = "queued" | "running" | "passed" | "failed" | "regression" | "error";

export interface RunStreamState {
  connected: boolean;
  events: RunEvent[];
  caseStatuses: Record<string, CaseStatus>;
  terminal: boolean;
}

const TERMINAL = new Set(["run_finished"]);

export function useRunStream(runId: number | null): RunStreamState {
  const [state, setState] = useState<RunStreamState>({
    connected: false,
    events: [],
    caseStatuses: {},
    terminal: false,
  });
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (runId == null) return;
    setState({ connected: false, events: [], caseStatuses: {}, terminal: false });

    const es = new EventSource(`/api/runs/${runId}/stream`);
    esRef.current = es;

    es.onopen = () => setState((s) => ({ ...s, connected: true }));
    es.onerror = () => setState((s) => ({ ...s, connected: false }));

    es.onmessage = (msg) => {
      let event: RunEvent;
      try {
        event = JSON.parse(msg.data);
      } catch {
        return;
      }

      setState((prev) => {
        const events = [...prev.events, event];

        // Update per-case status map.
        const next = { ...prev.caseStatuses };
        const name = event.payload && typeof event.payload === "object"
          ? (event.payload as Record<string, unknown>).case_name
          : undefined;

        if (event.kind === "run_started") {
          // Pre-populate the grid with "queued" entries from the suites list.
          const suites = (event.payload as { suites?: { cases: string[] }[] } | null)?.suites ?? [];
          for (const s of suites) {
            for (const c of s.cases ?? []) {
              if (!(c in next)) next[c] = "queued";
            }
          }
          // HTTP-mode: payload has a flat `cases` list of names instead.
          const flat = (event.payload as { cases?: string[] } | null)?.cases;
          if (flat) for (const c of flat) if (!(c in next)) next[c] = "queued";
        } else if (event.kind === "case_started" && typeof name === "string") {
          next[name] = "running";
        } else if (event.kind === "case_finished" && typeof name === "string") {
          const status = (event.payload as Record<string, unknown>).status;
          next[name] =
            (status === "regression" || status === "passed" || status === "failed" || status === "error")
              ? (status as CaseStatus)
              : "passed";
        } else if (event.kind === "case_error" && typeof name === "string") {
          next[name] = "error";
        }

        const terminal = TERMINAL.has(event.kind);
        if (terminal) es.close();

        return { connected: prev.connected, events, caseStatuses: next, terminal };
      });
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [runId]);

  return state;
}
