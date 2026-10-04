import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "./context/ThemeContext";
import { Shell } from "./components/layout/Shell";
import { DashboardPage } from "./pages/DashboardPage";
import { ResearchListPage } from "./pages/ResearchListPage";
import { ResearchDetailPage } from "./pages/ResearchDetailPage";
import { ExperimentListPage } from "./pages/ExperimentListPage";
import { ExperimentDetailPage } from "./pages/ExperimentDetailPage";
import { ExperimentComparePage } from "./pages/ExperimentComparePage";
import { EvidencePage } from "./pages/EvidencePage";
import { LineageDetailPage } from "./pages/LineageDetailPage";
import { VerificationPage } from "./pages/VerificationPage";
import { ReportListPage } from "./pages/ReportListPage";
import { ReportDetailPage } from "./pages/ReportDetailPage";
import { ArtifactListPage } from "./pages/ArtifactListPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { SettingsPage } from "./pages/SettingsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export const App: React.FC = () => {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Shell />}>
            {/* 1. Home / Command Center */}
            <Route index element={<DashboardPage />} />

            {/* 2. Research Routes */}
            <Route path="research" element={<ResearchListPage />} />
            <Route path="research/:runId" element={<ResearchDetailPage />} />

            {/* 3. Experiment Routes */}
            <Route path="experiments" element={<ExperimentListPage />} />
            <Route path="experiments/compare" element={<ExperimentComparePage />} />
            <Route path="experiments/:experimentId" element={<ExperimentDetailPage />} />

            {/* 4. Evidence & Lineage Routes */}
            <Route path="evidence" element={<EvidencePage />} />
            <Route path="evidence/claims" element={<EvidencePage />} />
            <Route path="evidence/verification" element={<VerificationPage />} />
            <Route path="evidence/lineage/:claimId" element={<LineageDetailPage />} />

            {/* 5. Reports Routes */}
            <Route path="reports" element={<ReportListPage />} />
            <Route path="reports/:reportId" element={<ReportDetailPage />} />

            {/* 6. Artifacts Routes */}
            <Route path="artifacts" element={<ArtifactListPage />} />
            <Route path="artifacts/:artifactId" element={<ArtifactListPage />} />

            {/* 7. Quality & Evaluation Center */}
            <Route path="evaluation" element={<EvaluationPage />} />

            {/* 8. Settings */}
            <Route path="settings" element={<SettingsPage />} />

            {/* 404 Fallback */}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
};

export default App;
