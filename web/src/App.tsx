import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { PageLoadingProvider } from "./components/PageLoading";
import { ExistingAuditsPage } from "./pages/ExistingAuditsPage";
import { LandingPage } from "./pages/LandingPage";
import { NewAuditPage } from "./pages/NewAuditPage";
import { ReportPage } from "./pages/ReportPage";
import { SinglePageAuditsPage } from "./pages/SinglePageAuditsPage";
import { SinglePageAuditDetailPage } from "./pages/SinglePageAuditDetailPage";

export default function App() {
  return (
    <AuthProvider>
      <PageLoadingProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<LandingPage />} />
            <Route path="audit/new" element={<NewAuditPage />} />
            <Route path="audits" element={<ExistingAuditsPage />} />
            <Route path="page-audits" element={<SinglePageAuditsPage />} />
            <Route path="page-audits/:parentId/:pageId" element={<SinglePageAuditDetailPage />} />
            <Route path="report/:auditId" element={<ReportPage />} />
            <Route path="report/:auditId/:section" element={<ReportPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </PageLoadingProvider>
    </AuthProvider>
  );
}
