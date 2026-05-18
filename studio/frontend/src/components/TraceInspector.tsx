// Chronological waterfall over a run's trace.
//
// Interleaves `llm_calls` and `tool_calls` by timestamp when both lists have
// timestamps; falls back to "LLM calls first, then tool calls in recorded
// order" with a small banner explaining why ordering isn't guaranteed.
//
// Cross-component anchoring contract:
//   - `highlightedStepId` ("t-{index}" / "l-{index}") highlights one row
//   - `onStepClick(stepId)` fires whenever the user clicks a row
// This lines up with the AssertionMatrix's `onAnchorTrace` callback so
// clicking a `tool_called('foo')` row in the matrix highlights the matching
// tool step here.

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/Card";

export interface LLMCall {
  timestamp?: string | null;
  latency_ms?: number | null;
  model?: string | null;
  provider?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  cost_usd?: number | null;
  output_text?: string | null;
  messages?: Array<{ role: string; content: string }> | null;
}

export interface ToolCall {
  timestamp?: string | null;
  latency_ms?: number | null;
  name: string;
  // The engine emits `arguments` (TraceJson) and most adapters emit `result`.
  // We accept both `input/output` and `arguments/result` for forward
  // compatibility.
  input?: unknown;
  arguments?: Record<string, unknown>;
  output?: unknown;
  result?: unknown;
  error?: string | null;
}

export interface TraceInspectorProps {
  llmCalls?: LLMCall[] | null;
  toolCalls?: ToolCall[] | null;
  totalCostUsd?: number | null;
  totalLatencyMs?: number | null;
  highlightedStepId?: string | null;
  onStepClick?: (stepId: string) => void;
}

type StepKind = "llm" | "tool";

interface Step {
  id: string;
  kind: StepKind;
  index: number;
  call: LLMCall | ToolCall;
  timestamp?: string | null;
  latency_ms?: number | null;
}

function mergeSteps(
  llmCalls: LLMCall[],
  toolCalls: ToolCall[],
): { steps: Step[]; orderedByTimestamp: boolean } {
  const allLlmHaveTs = llmCalls.length === 0 || llmCalls.every((c) => !!c.timestamp);
  const allToolHaveTs = toolCalls.length === 0 || toolCalls.every((c) => !!c.timestamp);
  const orderedByTimestamp = allLlmHaveTs && allToolHaveTs;
  const llmSteps: Step[] = llmCalls.map((c, i) => ({
    id: `l-${i}`,
    kind: "llm",
    index: i,
    call: c,
    timestamp: c.timestamp,
    latency_ms: c.latency_ms,
  }));
  const toolSteps: Step[] = toolCalls.map((c, i) => ({
    id: `t-${i}`,
    kind: "tool",
    index: i,
    call: c,
    timestamp: c.timestamp,
    latency_ms: c.latency_ms,
  }));
  if (orderedByTimestamp) {
    const all = [...llmSteps, ...toolSteps];
    all.sort((a, b) => {
      const ta = a.timestamp ? Date.parse(a.timestamp) : 0;
      const tb = b.timestamp ? Date.parse(b.timestamp) : 0;
      if (ta !== tb) return ta - tb;
      if (a.kind !== b.kind) return a.kind === "llm" ? -1 : 1;
      return a.index - b.index;
    });
    return { steps: all, orderedByTimestamp: true };
  }
  return { steps: [...llmSteps, ...toolSteps], orderedByTimestamp: false };
}

export function TraceInspector({
  llmCalls,
  toolCalls,
  totalCostUsd,
  totalLatencyMs,
  highlightedStepId,
  onStepClick,
}: TraceInspectorProps) {
  const lc = llmCalls ?? [];
  const tc = toolCalls ?? [];

  const { steps, orderedByTimestamp } = useMemo(() => mergeSteps(lc, tc), [lc, tc]);

  const maxLatency = useMemo(() => {
    let max = 0;
    for (const s of steps) if (s.latency_ms && s.latency_ms > max) max = s.latency_ms;
    return max;
  }, [steps]);

  const totals = useMemo(() => {
    let cost = 0;
    let latency = 0;
    for (const s of steps) {
      if (s.kind === "llm") cost += (s.call as LLMCall).cost_usd ?? 0;
      latency += s.latency_ms ?? 0;
    }
    return {
      cost: totalCostUsd ?? cost,
      latency: totalLatencyMs ?? latency,
    };
  }, [steps, totalCostUsd, totalLatencyMs]);

  if (steps.length === 0) return null;

  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border p-4">
        <h2 className="font-semibold">Trace</h2>
        <span className="font-mono text-xs text-muted-foreground">
          {lc.length} LLM call{lc.length === 1 ? "" : "s"} · {tc.length} tool call
          {tc.length === 1 ? "" : "s"}
        </span>
      </div>

      {!orderedByTimestamp && (
        <div className="border-b border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-4 py-2 text-xs text-[hsl(var(--warning))]">
          Chronological order unavailable (no per-step timestamps). Showing LLM
          calls followed by tool calls in recorded order.
        </div>
      )}

      <ol className="divide-y divide-border">
        {steps.map((step, i) => (
          <StepRow
            key={step.id}
            step={step}
            displayIndex={i + 1}
            maxLatency={maxLatency}
            highlighted={step.id === highlightedStepId}
            onClick={onStepClick}
          />
        ))}
      </ol>

      <div className="flex items-center justify-end gap-4 border-t border-border bg-muted/20 px-4 py-2 text-xs font-mono text-muted-foreground">
        <span>
          total: <strong className="font-semibold text-foreground">{formatLatency(totals.latency)}</strong>
        </span>
        <span>
          cost: <strong className="font-semibold text-foreground">{formatCost(totals.cost)}</strong>
        </span>
      </div>
    </Card>
  );
}

interface StepRowProps {
  step: Step;
  displayIndex: number;
  maxLatency: number;
  highlighted: boolean;
  onClick?: (stepId: string) => void;
}

function StepRow({ step, displayIndex, maxLatency, highlighted, onClick }: StepRowProps) {
  const [expanded, setExpanded] = useState(false);
  const isLlm = step.kind === "llm";
  const llm = isLlm ? (step.call as LLMCall) : null;
  const tool = !isLlm ? (step.call as ToolCall) : null;
  const name = isLlm ? llm?.model ?? llm?.provider ?? "llm" : tool?.name ?? "tool";
  const latency = step.latency_ms ?? 0;
  const widthPct = maxLatency > 0 ? Math.max(2, (latency / maxLatency) * 100) : 0;

  const handleClick = () => {
    setExpanded((v) => !v);
    onClick?.(step.id);
  };

  return (
    <li
      className={cn(
        "transition-colors",
        highlighted && "bg-[hsl(var(--warning))]/10",
        !!tool?.error && "bg-destructive/5",
      )}
    >
      <button
        type="button"
        onClick={handleClick}
        className="grid w-full grid-cols-[28px_56px_minmax(0,1fr)_minmax(80px,1fr)_72px_64px_18px] items-center gap-3 px-4 py-2 text-left hover:bg-muted/30"
        aria-expanded={expanded}
      >
        <span className="font-mono text-xs text-muted-foreground">#{displayIndex}</span>
        <span
          className={cn(
            "rounded-md border px-1.5 py-0.5 text-center font-mono text-[10px] font-semibold uppercase",
            isLlm
              ? "border-primary/30 bg-primary/10 text-primary"
              : "border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))]",
          )}
        >
          {isLlm ? "LLM" : "TOOL"}
        </span>
        <span className={cn("truncate font-mono text-xs", tool?.error && "text-destructive")}>
          {name}
        </span>
        <span className="h-1.5 overflow-hidden rounded-full bg-muted">
          <span
            className={cn(
              "block h-full rounded-full",
              isLlm ? "bg-primary" : "bg-[hsl(var(--warning))]",
            )}
            style={{ width: `${widthPct}%` }}
          />
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {formatLatency(latency)}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {isLlm && llm?.cost_usd != null ? formatCost(llm.cost_usd) : ""}
        </span>
        <span className="text-muted-foreground">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-dashed border-border bg-muted/10 px-5 py-3 text-xs">
          {isLlm ? <LlmBody call={llm!} /> : <ToolBody call={tool!} />}
        </div>
      )}
    </li>
  );
}

function LlmBody({ call }: { call: LLMCall }) {
  return (
    <div className="space-y-2">
      <KV k="model" v={call.model} />
      <KV k="provider" v={call.provider} />
      <KV k="prompt tokens" v={call.prompt_tokens} />
      <KV k="completion tokens" v={call.completion_tokens} />
      {call.cost_usd != null && <KV k="cost" v={formatCost(call.cost_usd)} />}
      <KV k="timestamp" v={call.timestamp} />
      {call.messages && call.messages.length > 0 && (
        <Section label="messages">
          <ol className="space-y-1.5">
            {call.messages.map((m, i) => (
              <li
                key={i}
                className={cn(
                  "rounded-md border px-2 py-1.5 font-mono",
                  m.role === "system" && "border-primary/30 bg-primary/5",
                  m.role === "user" && "border-[hsl(var(--success))]/30 bg-[hsl(var(--success))]/5",
                  m.role === "assistant" && "border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/5",
                  m.role === "tool" && "border-muted bg-muted/40",
                )}
              >
                <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {m.role}
                </div>
                <pre className="m-0 whitespace-pre-wrap break-words text-xs">{m.content}</pre>
              </li>
            ))}
          </ol>
        </Section>
      )}
      {call.output_text && (
        <Section label="output">
          <pre className="m-0 rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
            {call.output_text}
          </pre>
        </Section>
      )}
    </div>
  );
}

function ToolBody({ call }: { call: ToolCall }) {
  const input = call.input ?? call.arguments;
  const output = call.output ?? call.result;
  return (
    <div className="space-y-2">
      <KV k="name" v={call.name} />
      {call.latency_ms != null && <KV k="latency" v={formatLatency(call.latency_ms)} />}
      <KV k="timestamp" v={call.timestamp} />
      {call.error && (
        <Section label="error">
          <pre className="m-0 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 font-mono text-xs text-destructive">
            {call.error}
          </pre>
        </Section>
      )}
      {input !== undefined && input !== null && (
        <Section label="input">
          <pre className="m-0 max-h-72 overflow-auto rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
            {formatValue(input)}
          </pre>
        </Section>
      )}
      {output !== undefined && output !== null && (
        <Section label="output">
          <pre className="m-0 max-h-72 overflow-auto rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
            {formatValue(output)}
          </pre>
        </Section>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  if (v == null || v === "") return null;
  return (
    <div className="inline-flex items-baseline gap-1.5 font-mono">
      <span className="text-muted-foreground">{k}</span>
      <span>{v}</span>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  );
}

function formatLatency(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatCost(usd: number): string {
  if (usd === 0) return "$0.0000";
  if (usd < 0.0001) return `$${usd.toExponential(2)}`;
  return `$${usd.toFixed(4)}`;
}

function formatValue(v: unknown): string {
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// Used by AssertionMatrix's anchor button: when the grader is
// tool_called('foo'), the step id is "t-{index of foo in tool_calls}".
export type AnchorContract = `t-${number}` | `l-${number}`;
