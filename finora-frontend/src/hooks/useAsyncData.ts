import { useCallback, useEffect, useRef, useState } from 'react';
import type { DependencyList } from 'react';

export function useAsyncData<T>(loader: () => Promise<T>, deps: DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const versionRef = useRef(0);
  const activeRef = useRef(true);

  const reload = useCallback(async () => {
    const version = ++versionRef.current;
    setLoading(true);
    setError(null);
    try {
      const next = await loader();
      // 只有最新一次请求且组件仍挂载时才写入，避免旧响应覆盖新数据/卸载后 setState。
      if (version !== versionRef.current || !activeRef.current) return;
      setData(next);
    } catch (caught) {
      if (version !== versionRef.current || !activeRef.current) return;
      setError(caught instanceof Error ? caught : new Error(String(caught)));
      setData(null);
    } finally {
      if (version === versionRef.current && activeRef.current) setLoading(false);
    }
  }, deps);

  useEffect(() => {
    activeRef.current = true;
    void reload();
    return () => { activeRef.current = false; versionRef.current += 1; };
  }, [reload]);

  return { data, loading, error, reload, setData };
}
