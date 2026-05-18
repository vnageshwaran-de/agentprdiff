import { Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { BaselineReviewPage } from "./pages/BaselineReview";
import { CaseDetail } from "./pages/CaseDetail";
import { CaseTimelinePage } from "./pages/CaseTimeline";
import { CoverageHeatmapPage } from "./pages/CoverageHeatmap";
import { ModelBenchmarkPage } from "./pages/ModelBenchmark";
import { ProjectDetail } from "./pages/ProjectDetail";
import { ProjectNew } from "./pages/ProjectNew";
import { ProjectsList } from "./pages/ProjectsList";
import { ReplaySandboxPage } from "./pages/ReplaySandbox";
import { ReviewProposalsPage } from "./pages/ReviewProposals";
import { RunDetail } from "./pages/RunDetail";
import { SecretsPage } from "./pages/SecretsPage";
import { SuiteHealthPage } from "./pages/SuiteHealth";
import { TourPage } from "./pages/TourPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<ProjectsList />} />
        <Route path="/projects/new" element={<ProjectNew />} />
        <Route path="/projects/:id" element={<ProjectDetail />} />
        <Route path="/projects/:id/tour" element={<TourPage />} />
        <Route path="/projects/:id/health" element={<SuiteHealthPage />} />
        <Route path="/projects/:id/baselines" element={<BaselineReviewPage />} />
        <Route path="/projects/:id/coverage" element={<CoverageHeatmapPage />} />
        <Route path="/projects/:id/review" element={<ReviewProposalsPage />} />
        <Route
          path="/suites/:suiteId/benchmark"
          element={<ModelBenchmarkPage />}
        />
        <Route
          path="/suites/:suiteId/cases/:caseName/timeline"
          element={<CaseTimelinePage />}
        />
        <Route path="/runs/:id" element={<RunDetail />} />
        <Route path="/runs/:runId/cases/:caseRunId" element={<CaseDetail />} />
        <Route
          path="/runs/:runId/cases/:caseRunId/replay"
          element={<ReplaySandboxPage />}
        />
        <Route path="/secrets" element={<SecretsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function NotFound() {
  return (
    <div className="rounded-lg border border-border bg-card p-12 text-center">
      <h2 className="text-lg font-semibold">Not found</h2>
      <p className="text-sm text-muted-foreground">That route doesn't exist.</p>
    </div>
  );
}
