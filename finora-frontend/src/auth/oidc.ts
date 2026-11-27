import { User, UserManager, WebStorageStateStore } from 'oidc-client-ts';

const authority = import.meta.env.VITE_OIDC_AUTHORITY || '';
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || '';
const scope = import.meta.env.VITE_OIDC_SCOPE || 'openid profile email';
const redirectUri = import.meta.env.VITE_OIDC_REDIRECT_URI || `${window.location.origin}/auth/callback`;
const postLogoutRedirectUri = import.meta.env.VITE_OIDC_POST_LOGOUT_URI || window.location.origin;

let manager: UserManager | null = null;
let cachedUser: User | null = null;
let callbackPromise: Promise<User> | null = null;
let callbackUser: User | null = null;
let renewing: Promise<string | null> | null = null;

function getManager(): UserManager {
  if (!authority || !clientId) {
    throw new Error('OIDC 未配置：请设置 VITE_OIDC_AUTHORITY 与 VITE_OIDC_CLIENT_ID');
  }
  if (!manager) {
    manager = new UserManager({
      authority,
      client_id: clientId,
      redirect_uri: redirectUri,
      post_logout_redirect_uri: postLogoutRedirectUri,
      response_type: 'code',
      scope,
      automaticSilentRenew: true,
      silentRequestTimeoutInSeconds: 10,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    });
    manager.events.addUserLoaded((user) => { cachedUser = user; });
    manager.events.addUserUnloaded(() => { cachedUser = null; });
  }
  return manager;
}

export function isOidcConfigured(): boolean {
  return Boolean(authority && clientId);
}

export function setCachedUser(user: User | null): void {
  cachedUser = user;
}

export function getCachedUser(): User | null {
  return cachedUser;
}

export function getAccessToken(): string | null {
  return cachedUser?.access_token || null;
}

export async function renewToken(): Promise<string | null> {
  if (!isOidcConfigured()) return null;
  if (renewing) return renewing;
  renewing = (async () => {
    const instance = getManager();
    const current = await instance.getUser();
    if (!current) {
      cachedUser = null;
      return null;
    }
    try {
      const user = await instance.signinSilent();
      if (!user) {
        cachedUser = null;
        return null;
      }
      cachedUser = user;
      return user.access_token;
    } catch {
      await clearUser();
      return null;
    } finally {
      renewing = null;
    }
  })();
  return renewing;
}

export async function ensureFreshToken(): Promise<string | null> {
  if (!isOidcConfigured()) return null;
  const instance = getManager();
  const user = await instance.getUser();
  if (!user) {
    cachedUser = null;
    return null;
  }
  if (user.expired) return renewToken();
  cachedUser = user;
  return user.access_token;
}

export async function signIn(): Promise<void> {
  callbackPromise = null;
  callbackUser = null;
  await getManager().signinRedirect();
}

export async function completeSignIn(): Promise<User> {
  if (callbackUser) return callbackUser;
  if (!callbackPromise) {
    callbackPromise = getManager().signinRedirectCallback()
      .then((user) => {
        callbackUser = user;
        cachedUser = user;
        return user;
      })
      .catch((error: unknown) => {
        callbackPromise = null;
        throw error;
      });
  }
  return callbackPromise;
}

export async function clearUser(): Promise<void> {
  cachedUser = null;
  callbackPromise = null;
  callbackUser = null;
  renewing = null;
  if (isOidcConfigured()) {
    try {
      await getManager().removeUser();
    } catch {
      // 即使 OIDC provider 不可达，也要清掉本地缓存，避免 401 循环。
    }
  }
}

export async function signOut(): Promise<void> {
  await clearUser();
  if (isOidcConfigured()) await getManager().signoutRedirect();
}
