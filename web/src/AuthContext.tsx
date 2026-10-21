import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, AUTH_SESSION_EXPIRED_EVENT, getCurrentUser, logout, type AuthProfile } from "./api";

interface AuthContextValue {
  user: AuthProfile | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  acceptLogin: (profile: AuthProfile) => void;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUser(await getCurrentUser());
    } catch (reason) {
      setUser(null);
      if (!(reason instanceof ApiError && reason.status === 401)) {
        setError("无法连接认证服务，请检查服务状态后重试。");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const handleExpiredSession = () => setUser(null);
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession);
    return () => window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession);
  }, []);

  const acceptLogin = useCallback((profile: AuthProfile) => {
    setUser(profile);
    setError(null);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logout();
    } catch {
      // The server session will expire if logout cannot reach the API.
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, error, refresh, acceptLogin, signOut }),
    [user, loading, error, refresh, acceptLogin, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
