import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderGit2, FileArchive, Globe, ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Label, Textarea } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { api, ApiError } from "@/api/client";
import { cn } from "@/lib/cn";
import type { IntakeMode } from "@/api/types";

type Step = "pick" | "configure";

export function ProjectNew() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [step, setStep] = useState<Step>("pick");
  const [mode, setMode] = useState<IntakeMode>("git");
  const [name, setName] = useState("");

  // git fields
  const [gitUrl, setGitUrl] = useState("");
  const [gitRef, setGitRef] = useState("");

  // zip fields
  const [zipFile, setZipFile] = useState<File | null>(null);

  // http fields
  const [httpUrl, setHttpUrl] = useState("");
  const [httpMethod, setHttpMethod] = useState("POST");
  const [httpHeaders, setHttpHeaders] = useState("{}");
  const [httpBody, setHttpBody] = useState('{"input": "{{input}}"}');
  const [httpOutputPath, setHttpOutputPath] = useState("data.reply");

  const create = useMutation({
    mutationFn: async () => {
      if (mode === "git") {
        return api.createProject({
          name,
          intake_mode: "git",
          source: gitUrl,
          git_ref: gitRef || null,
        });
      }
      if (mode === "zip") {
        if (!zipFile) throw new Error("pick a .zip file");
        return api.uploadProject(name, zipFile);
      }
      // http
      const headers = httpHeaders.trim() ? JSON.parse(httpHeaders) : {};
      const body_template = httpBody.trim() ? JSON.parse(httpBody) : null;
      return api.createProject({
        name,
        intake_mode: "http",
        source: httpUrl,
        http_config: {
          url: httpUrl,
          method: httpMethod,
          headers,
          body_template,
          output_path: httpOutputPath,
        },
      });
    },
    onSuccess: (project) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}`);
    },
  });

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <button
        onClick={() => (step === "pick" ? navigate("/") : setStep("pick"))}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back
      </button>

      {step === "pick" && (
        <Card>
          <CardHeader>
            <CardTitle>Connect your project</CardTitle>
            <CardDescription>
              Choose how Studio should pull in the code or endpoint to test.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-3">
            <ModeCard
              icon={FolderGit2}
              label="Git repository"
              hint="Clone a public or private repo"
              active={mode === "git"}
              onClick={() => setMode("git")}
            />
            <ModeCard
              icon={FileArchive}
              label="Upload a zip"
              hint="Send a folder from your machine"
              active={mode === "zip"}
              onClick={() => setMode("zip")}
            />
            <ModeCard
              icon={Globe}
              label="HTTP endpoint"
              hint="Test an agent you've already deployed"
              active={mode === "http"}
              onClick={() => setMode("http")}
            />
            <div className="sm:col-span-3 flex justify-end">
              <Button onClick={() => setStep("configure")}>Continue</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {step === "configure" && (
        <Card>
          <CardHeader>
            <CardTitle>
              Configure {mode === "git" ? "git" : mode === "zip" ? "upload" : "HTTP"} project
            </CardTitle>
            <CardDescription>
              {mode === "git" && "Studio clones the repo and walks it for suites."}
              {mode === "zip" && "Studio extracts your zip into an isolated workspace."}
              {mode === "http" && "Suite authoring happens after the project is created."}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. checkout-agent" />
            </Field>

            {mode === "git" && (
              <>
                <Field label="Clone URL">
                  <Input
                    value={gitUrl}
                    onChange={(e) => setGitUrl(e.target.value)}
                    placeholder="https://github.com/you/repo.git"
                  />
                </Field>
                <Field label="Ref (optional)">
                  <Input
                    value={gitRef}
                    onChange={(e) => setGitRef(e.target.value)}
                    placeholder="main"
                  />
                </Field>
              </>
            )}

            {mode === "zip" && (
              <Field label="Archive">
                <input
                  type="file"
                  accept=".zip,application/zip"
                  onChange={(e) => setZipFile(e.target.files?.[0] ?? null)}
                  className="text-sm"
                />
              </Field>
            )}

            {mode === "http" && (
              <>
                <Field
                  label="Endpoint URL"
                  hint="Studio sends each test case to this URL and grades the response."
                >
                  <Input value={httpUrl} onChange={(e) => setHttpUrl(e.target.value)} placeholder="https://api.example.com/agent" />
                </Field>
                <Field label="Method" hint="POST is almost always right.">
                  <Input value={httpMethod} onChange={(e) => setHttpMethod(e.target.value)} />
                </Field>
                <Field
                  label="Headers (JSON)"
                  hint='Plain {"key": "value"} map. For auth: {"Authorization": "Bearer …"}.'
                >
                  <Textarea value={httpHeaders} onChange={(e) => setHttpHeaders(e.target.value)} rows={3} />
                </Field>
                <Field
                  label="Body template (JSON)"
                  hint={'Studio substitutes {{input}} with each case\'s input. Example: {"messages":[{"role":"user","content":"{{input}}"}]}.'}
                >
                  <Textarea value={httpBody} onChange={(e) => setHttpBody(e.target.value)} rows={4} />
                </Field>
                <Field
                  label="Response output path"
                  hint='Dotted path into the JSON response. "data.reply" picks {"data":{"reply":"…"}}. Empty = use the whole body.'
                >
                  <Input value={httpOutputPath} onChange={(e) => setHttpOutputPath(e.target.value)} />
                </Field>
              </>
            )}

            {create.error && (
              <p className="text-sm text-destructive">
                {create.error instanceof ApiError
                  ? typeof create.error.detail === "string"
                    ? create.error.detail
                    : create.error.message
                  : String(create.error)}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setStep("pick")}>Back</Button>
              <Button
                onClick={() => create.mutate()}
                disabled={create.isPending || !name}
              >
                {create.isPending && <Spinner />}
                Create project
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ModeCard({
  icon: Icon,
  label,
  hint,
  active,
  onClick,
}: {
  icon: typeof FolderGit2;
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border p-4 text-left transition-colors",
        "hover:bg-muted/60",
        active ? "border-ring ring-2 ring-ring/30" : "border-border",
      )}
    >
      <Icon className="h-5 w-5" />
      <div>
        <div className="font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{hint}</div>
      </div>
    </button>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
