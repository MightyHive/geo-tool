import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { ExistingAuditsPage } from "./pages/ExistingAuditsPage";
import { LandingPage } from "./pages/LandingPage";
import { NewAuditPage } from "./pages/NewAuditPage";
import { ReportPage } from "./pages/ReportPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<LandingPage />} />
          <Route path="audit/new" element={<NewAuditPage />} />
          <Route path="audits" element={<ExistingAuditsPage />} />
          <Route path="report/:auditId" element={<ReportPage />} />
          <Route path="report/:auditId/:section" element={<ReportPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
