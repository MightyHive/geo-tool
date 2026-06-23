import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { fetchAuthStatus, logoutApi } from "../api/client";
import type { AuthMode, AuthStatus, AuthUser } from "../types";

interface AuthContextValue {
  mode: AuthMode;
  enabled: boolean;
  loggedIn: boolean;
  user: AuthUser | null;
  loading: boolean;
  logoutAvailable: boolean;
  iapEnforce: boolean;
  login: (returnTo?: string) => void;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const EMPTY_STATUS: AuthStatus = {
  mode: "none",
  enabled: false,
  logged_in: false,
  user: null,
  login_url: null,
  logout_available: false,
  iap_enforce: false,
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchAuthStatus();
      setStatus(s);
    } catch {
      setStatus(EMPTY_STATUS);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    (returnTo = "/") => {
      if (status?.mode === "iap") {
        return;
      }
      const path = returnTo.startsWith("/") ? returnTo : "/";
      window.location.href = `/api/auth/login?return_to=${encodeURIComponent(path)}`;
    },
    [status?.mode],
  );

  const logout = useCallback(async () => {
    if (status?.mode !== "oauth") {
      return;
    }
    await logoutApi();
    await refresh();
  }, [status?.mode, refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({
      mode: status?.mode ?? "none",
      enabled: status?.enabled ?? false,
      loggedIn: status?.logged_in ?? false,
      user: status?.user ?? null,
      loading,
      logoutAvailable: status?.logout_available ?? false,
      iapEnforce: status?.iap_enforce ?? false,
      login,
      logout,
      refresh,
    }),
    [status, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
