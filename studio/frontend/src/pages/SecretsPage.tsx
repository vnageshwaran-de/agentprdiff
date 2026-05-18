import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";

import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toaster";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, Label } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";

export function SecretsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["secrets"], queryFn: api.listSecrets });

  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [scope, setScope] = useState("global");

  const upsert = useMutation({
    mutationFn: () => api.upsertSecret({ name, value, scope }),
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: ["secrets"] });
      setName("");
      setValue("");
      toast.push({
        kind: "success",
        title: `Saved ${row.name}`,
        description: `Scope: ${row.scope}. The value is encrypted at rest and never returned by the API.`,
      });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't save secret",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSecret(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["secrets"] });
      toast.push({ kind: "info", title: "Secret deleted" });
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Secrets</h1>
        <p className="text-sm text-muted-foreground">
          API keys and env vars Studio injects into runs. Values are encrypted at
          rest and never returned by the API once stored — to rotate, just add
          again.
        </p>
      </div>

      <Card className="p-4 space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_140px]">
          <div className="grid gap-1.5">
            <Label>Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="OPENAI_API_KEY"
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Value</Label>
            <Input
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="sk-…"
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Scope</Label>
            <Input
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              placeholder="global or project:N"
            />
          </div>
        </div>
        {upsert.error && (
          <p className="text-sm text-destructive">
            {upsert.error instanceof ApiError
              ? typeof upsert.error.detail === "string"
                ? upsert.error.detail
                : upsert.error.message
              : String(upsert.error)}
          </p>
        )}
        <div className="flex justify-end">
          <Button
            onClick={() => upsert.mutate()}
            disabled={!name || !value || upsert.isPending}
          >
            {upsert.isPending ? <Spinner /> : <Plus className="h-4 w-4" />}
            Save secret
          </Button>
        </div>
      </Card>

      <Card>
        <div className="border-b border-border p-4 font-semibold">Stored secrets</div>
        {list.isLoading && (
          <div className="p-12 text-center text-sm text-muted-foreground">
            <Spinner className="mx-auto mb-2" /> Loading…
          </div>
        )}
        {list.data && list.data.length === 0 && (
          <div className="p-12 text-center text-sm text-muted-foreground">
            No secrets stored yet.
          </div>
        )}
        {list.data && list.data.length > 0 && (
          <ul>
            {list.data.map((s) => (
              <li
                key={s.id}
                className="flex items-center justify-between border-b border-border px-4 py-3 last:border-0"
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm">{s.name}</span>
                  <Badge tone="neutral">{s.scope}</Badge>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove.mutate(s.id)}
                  disabled={remove.isPending}
                  aria-label={`delete ${s.name}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
