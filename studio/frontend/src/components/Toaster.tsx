// Tiny dep-free toaster. Provider + useToast() hook. Three slots max, each
// auto-dismisses after a few seconds. No queues, no positioning props — just
// enough to confirm a save or surface a friendly error without yanking in
// react-hot-toast / sonner / radix.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { CheckCircle2, AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/cn";

export type ToastKind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
}

interface ToastCtx {
  push: (t: Omit<Toast, "id">) => void;
}

const Ctx = createContext<ToastCtx | null>(null);

const MAX = 3;
const TTL = 5_000;

export function ToasterProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((t: Omit<Toast, "id">) => {
    setToasts((prev) => {
      const id = Date.now() + Math.random();
      const next = [...prev, { ...t, id }].slice(-MAX);
      return next;
    });
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Auto-dismiss.
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((t) => setTimeout(() => dismiss(t.id), TTL));
    return () => timers.forEach(clearTimeout);
  }, [toasts, dismiss]);

  const value = useMemo<ToastCtx>(() => ({ push }), [push]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2"
      >
        {toasts.map((t) => (
          <ToastView key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </Ctx.Provider>
  );
}

function ToastView({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const Icon = toast.kind === "success" ? CheckCircle2 : AlertTriangle;
  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto flex items-start gap-2 rounded-md border p-3 shadow-md bg-card",
        toast.kind === "success" && "border-[hsl(var(--success))]/40",
        toast.kind === "error" && "border-destructive/50",
        toast.kind === "info" && "border-border",
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 h-4 w-4 shrink-0",
          toast.kind === "success" && "text-[hsl(var(--success))]",
          toast.kind === "error" && "text-destructive",
          toast.kind === "info" && "text-muted-foreground",
        )}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{toast.title}</div>
        {toast.description && (
          <div className="mt-0.5 text-xs text-muted-foreground">{toast.description}</div>
        )}
      </div>
      <button
        onClick={onDismiss}
        className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label="dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function useToast(): ToastCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be used inside <ToasterProvider>");
  return ctx;
}
