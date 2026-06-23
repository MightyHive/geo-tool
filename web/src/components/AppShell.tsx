import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  Database,
  FileBarChart,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  Plus,
  X,
} from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { fetchConfig } from "../api/client";
import { loadAuditRunDraft } from "../lib/wizardDraft";
import { cn } from "../lib/utils";

const navLinks = [
  { to: "/", label: "Home", icon: LayoutDashboard, end: true as const },
  { to: "/audit/new?fresh=1", label: "New audit", icon: Plus },
  { to: "/audits", label: "Existing audits", icon: Database },
];

function Logo({ compact }: { compact?: boolean }) {
  return (
    <NavLink to="/" className="flex items-center gap-2 py-1 relative z-20">
      <div className="h-6 w-6 bg-[#efede9] rounded-lg flex-shrink-0 flex items-center justify-center">
        <span className="text-[#2d2d2d] font-bold text-[10px]">GEO</span>
      </div>
      {!compact && (
        <span className="font-semibold text-white whitespace-pre">GEO Audit</span>
      )}
    </NavLink>
  );
}

function SidebarNav({
  open,
  onNavigate,
}: {
  open: boolean;
  onNavigate?: () => void;
}) {
  return (
    <nav className="mt-8 flex flex-col gap-1">
      {navLinks.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2 py-2 px-2 rounded-lg transition-colors",
              isActive
                ? "bg-neutral-700 text-white"
                : "text-neutral-200 hover:bg-neutral-700/60",
            )
          }
        >
          <Icon className="h-5 w-5 flex-shrink-0" />
          {open && (
            <span className="text-sm whitespace-pre">{label}</span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function UserFooter({
  user,
  mode,
  logoutAvailable,
  onLogout,
  compact,
}: {
  user: { email: string; name: string } | null;
  mode: string;
  logoutAvailable: boolean;
  onLogout: () => void;
  compact?: boolean;
}) {
  if (!user) return null;
  const initial = user.email.charAt(0).toUpperCase();
  return (
    <div className="border-t border-neutral-700 pt-4 mt-4">
      {logoutAvailable && (
        <button
          type="button"
          onClick={onLogout}
          className="flex items-center gap-2 py-2 w-full text-neutral-200 hover:text-white text-sm mb-2"
        >
          <LogOut className="h-5 w-5 flex-shrink-0" />
          {!compact && <span>Sign out</span>}
        </button>
      )}
      <div className="flex items-center gap-2 px-1 py-2 min-w-0">
        <div className="h-7 w-7 flex-shrink-0 rounded-full bg-gradient-to-br from-blue-400 to-indigo-500 flex items-center justify-center text-white font-bold text-xs">
          {initial}
        </div>
        {!compact && (
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-neutral-200 truncate">
              {user.name || user.email.split("@")[0]}
            </p>
            <p className="text-xs text-neutral-400 truncate">
              {mode === "iap" ? "IAP" : "Signed in"}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function AppShell() {
  const { mode, user, loading, logoutAvailable, logout } = useAuth();
  const location = useLocation();
  const isReportView = location.pathname.startsWith("/report/");
  const [envLabel, setEnvLabel] = useState<string>();
  const [desktopExpanded, setDesktopExpanded] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [activeAuditRun, setActiveAuditRun] = useState(
    () => loadAuditRunDraft(),
  );

  useEffect(() => {
    fetchConfig()
      .then((c) => setEnvLabel(c.app_env_label))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    setActiveAuditRun(loadAuditRunDraft());
    const id = window.setInterval(() => setActiveAuditRun(loadAuditRunDraft()), 4000);
    return () => window.clearInterval(id);
  }, [location.pathname]);

  if (loading) {
    return (
      <div className="min-h-screen bg-brand-light flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 animate-spin text-brand-accent mx-auto mb-4" />
          <p className="text-gray-600">Loading…</p>
        </div>
      </div>
    );
  }

  const showExpanded = desktopExpanded;

  return (
    <div className="flex flex-col md:flex-row bg-gray-50 w-full h-screen overflow-hidden">
      <div className="md:hidden h-12 px-4 flex items-center justify-between bg-neutral-800 shrink-0">
        <Logo compact />
        <button
          type="button"
          className="text-neutral-200"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="Menu"
        >
          {mobileOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </div>

      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-50 bg-neutral-900 p-6 flex flex-col justify-between">
          <div>
            <button
              type="button"
              className="absolute right-6 top-6 text-neutral-200"
              onClick={() => setMobileOpen(false)}
            >
              <X className="h-6 w-6" />
            </button>
            <Logo />
            <SidebarNav open onNavigate={() => setMobileOpen(false)} />
          </div>
          <UserFooter
            user={user}
            mode={mode}
            logoutAvailable={logoutAvailable}
            onLogout={() => void logout()}
          />
        </div>
      )}

      <aside
        className={cn(
          "hidden md:flex flex-col bg-neutral-800 text-neutral-200 flex-shrink-0 transition-[width] duration-300 ease-in-out px-4 py-4",
          showExpanded ? "w-[260px]" : "w-[68px]",
        )}
        onMouseEnter={() => setDesktopExpanded(true)}
        onMouseLeave={() => setDesktopExpanded(false)}
      >
        <Logo compact={!showExpanded} />
        <SidebarNav open={showExpanded} />
        <div className="mt-auto">
          <UserFooter
            user={user}
            mode={mode}
            logoutAvailable={logoutAvailable}
            onLogout={() => void logout()}
            compact={!showExpanded}
          />
        </div>
      </aside>

      <div
        className={cn(
          "flex flex-1 flex-col overflow-hidden",
          isReportView ? "bg-[#fafafa]" : "bg-brand-light",
        )}
      >
        {!isReportView && (
          <header className="hidden md:flex items-center justify-between px-6 py-3 bg-white/60 border-b border-gray-200/80 backdrop-blur-sm shrink-0">
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <FileBarChart className="h-4 w-4 text-brand-accent" />
              <span>Generative Engine Optimization</span>
            </div>
            {envLabel && envLabel !== "Production" && (
              <span className="text-xs font-medium px-2 py-1 rounded-md bg-amber-100 text-amber-900">
                {envLabel}
              </span>
            )}
          </header>
        )}
        <main
          className={cn(
            "flex-1 min-h-0",
            isReportView ? "overflow-hidden flex flex-col" : "overflow-auto",
          )}
        >
          {activeAuditRun && !isReportView && (
            <div className="mx-4 md:mx-6 mt-4 alert-info text-sm">
              Audit in progress for{" "}
              <strong>{activeAuditRun.brandName || activeAuditRun.brandWebsite || "your site"}</strong>
              . You can leave this page — it runs in the background.{" "}
              <Link to={`/audit/new?step=7`} className="underline font-medium">
                View progress
              </Link>
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
