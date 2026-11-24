import { createContext, useContext } from 'react';
import type { DemoRole } from '../api';
import type { DataMode, Identity } from '../types';

export type NotifyType = 'success' | 'error' | 'warning' | 'info';

export interface AppContextValue {
  isMockMode: boolean;
  dataMode: DataMode;
  identity: Identity;
  apiHealthy: boolean | null;
  refreshToken: number;
  requestRefresh: () => void;
  notify: (type: NotifyType, content: string) => void;
  switchIdentity: (role: DemoRole) => void;
  switchTenant: (tenantId: string) => void;
}

export const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppContext');
  return value;
}
