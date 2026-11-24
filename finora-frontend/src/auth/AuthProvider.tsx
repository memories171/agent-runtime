import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { User } from 'oidc-client-ts';
import { api, isMockMode, setApiTenant, setAuthHandlers } from '../api';
import { clearUser, completeSignIn, ensureFreshToken, getCachedUser, isOidcConfigured, setCachedUser, signIn, signOut } from './oidc';

interface AuthTenant {
  tenant_id: string;
  display_name: string;
  organization_id?: string | null;
  status: string;
}

interface AuthProfile {
  user_id: string;
  tenant_id: string;
  display_name?: string;
  organization_id?: string | null;
  roles: string[];
  permissions: string[];
  scopes: string[];
  authentication_method: string;
  token_expires_at?: string | null;
}

interface AuthContextValue {
  ready: boolean;
  isApiMode: boolean;
  isAuthenticated: boolean;
  authError: string | null;
  user: User | null;
  profile: AuthProfile | null;
  tenants: AuthTenant[];
  currentTenant: string | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  switchTenant: (tenantId: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<AuthProfile | null>(null);
  const [tenants, setTenants] = useState<AuthTenant[]>([]);
  const [currentTenant, setCurrentTenant] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const callbackHandled = useRef(false);
  const justCompletedSignIn = useRef(false);

  useEffect(() => {
    if (isMockMode) {
      setReady(true);
      return;
    }
    setAuthHandlers({
      onUnauthorized: () => {
        void clearUser();
        setUser(null);
        setCachedUser(null);
        setProfile(null);
        setApiTenant(null);
      },
    });
    void (async () => {
      const isCallback = window.location.pathname === '/auth/callback';
      try {
        if (isCallback && !callbackHandled.current) {
          callbackHandled.current = true;
          justCompletedSignIn.current = true;
          await completeSignIn();
          window.history.replaceState({}, document.title, '/');
        } else {
          // 普通加载（含刷新页面）时保留会话存储中的用户，由 ensureFreshToken 恢复，
          // 避免每次刷新或页面内跳转都把用户踢回登录页。
          justCompletedSignIn.current = false;
        }
        if (isOidcConfigured()) {
          const token = await ensureFreshToken();
          if (token) {
            const tokenUser = getCachedUser();
            const me = await api.authMe() as unknown as AuthProfile;
            setUser(tokenUser);
            setProfile(me);
            setCurrentTenant(me.tenant_id);
            setApiTenant(me.tenant_id);
            setTenants(await api.authTenants() as AuthTenant[]);
            window.sessionStorage.removeItem('finora-oidc-callback-retry');
          }
        }
      } catch (error) {
        void clearUser();
        setUser(null);
        setProfile(null);
        setCurrentTenant(null);
        setApiTenant(null);
        if (isCallback) {
          window.history.replaceState({}, document.title, '/');
          const retryKey = 'finora-oidc-callback-retry';
          if (window.sessionStorage.getItem(retryKey) !== '1') {
            window.sessionStorage.setItem(retryKey, '1');
            await signIn();
            return;
          }
          window.sessionStorage.removeItem(retryKey);
          setAuthError('登录回调已失效，请重新登录');
        } else {
          setAuthError(error instanceof Error ? error.message : '认证初始化失败');
        }
      } finally {
        setReady(true);
      }
    })();
    return () => setAuthHandlers({});
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    ready,
    isApiMode: !isMockMode,
    isAuthenticated: Boolean(user && profile),
    authError,
    user,
    profile,
    tenants,
    currentTenant,
    signIn: async () => {
      setAuthError(null);
      await signIn();
    },
    signOut: async () => {
      await signOut();
      setUser(null);
      setProfile(null);
      setCurrentTenant(null);
      setApiTenant(null);
      setAuthError(null);
    },
    switchTenant: async (tenantId: string) => {
      setApiTenant(tenantId);
      try {
        const result = await api.switchTenant({ tenant_id: tenantId }) as unknown as AuthProfile;
        setCurrentTenant(result.tenant_id);
        setProfile(result);
        setApiTenant(result.tenant_id);
      } catch (error) {
        setApiTenant(currentTenant);
        throw error;
      }
    },
  }), [ready, user, profile, tenants, currentTenant, authError]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
