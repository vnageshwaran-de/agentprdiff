import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, ArrowRight, FolderGit2, FileArchive, Globe } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { api } from "@/api/client";
import type { IntakeMode } from "@/api/types";

const INTAKE_ICON: Record<IntakeMode, typeof FolderGit2> = {
  git: FolderGit2,
  zip: FileArchive,
  http: Globe,
};

export function ProjectsList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-muted-foreground">
            Each project is one repo, upload, or endpoint you're testing.
          </p>
        </div>
        <Link to="/projects/new">
          <Button>
            <Plus className="h-4 w-4" /> New project
          </Button>
        </Link>
      </div>

      {isLoading && (
        <Card className="p-12 text-center text-sm text-muted-foreground">
          <Spinner className="mx-auto mb-2" /> Loading…
        </Card>
      )}

      {error && (
        <Card className="border-destructive/40 p-6">
          <p className="text-sm text-destructive">Couldn't load projects: {String(error)}</p>
        </Card>
      )}

      {data && data.length === 0 && (
        <Card className="p-12 text-center">
          <p className="font-medium">No projects yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Connect a repo, upload a zip, or point Studio at an HTTP endpoint to get started.
          </p>
          <Link to="/projects/new" className="mt-4 inline-block">
            <Button>Create your first project</Button>
          </Link>
        </Card>
      )}

      {data && data.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {data.map((p) => {
            const Icon = INTAKE_ICON[p.intake_mode];
            return (
              <Link key={p.id} to={`/projects/${p.id}`} className="block">
                <Card className="p-4 transition-colors hover:bg-muted/40">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                        <span className="font-medium">{p.name}</span>
                        <Badge tone="neutral">{p.intake_mode}</Badge>
                      </div>
                      <p className="mt-1 truncate text-sm text-muted-foreground">{p.source}</p>
                    </div>
                    <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </div>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
