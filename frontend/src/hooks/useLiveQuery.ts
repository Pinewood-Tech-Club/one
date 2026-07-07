'use client';

// Event-driven query hook: fetch on mount, apply/refetch on app events,
// refetch on reconnect + focus. Polls only as a backstop (60s while the
// event channel is open, 30s while it is down).

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '@/lib/api';
import { subscribeAppEvents, useEventChannelState } from './useAppEvents';

const REFETCH_DEBOUNCE_MS = 500;

export type LiveQueryEventConfig<T> = {
  type: string;
  // Omitted (or 'refetch' outcome) → debounced refetch; otherwise the
  // returned value replaces the current data without a round-trip.
  apply?: (data: Record<string, unknown>, prev: T | undefined) => T | 'refetch';
};

export type LiveQueryOptions<T> = {
  fetcher: () => Promise<T>;
  events?: LiveQueryEventConfig<T>[];
  enabled?: boolean;
  deps?: unknown[];
  backstopMs?: number;
  degradedPollMs?: number;
  refetchOnFocus?: boolean;
};

export type LiveQueryResult<T> = {
  data: T | undefined;
  error: ApiError | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
};

export function useLiveQuery<T>(opts: LiveQueryOptions<T>): LiveQueryResult<T> {
  const {
    enabled = true,
    deps = [],
    backstopMs = 60_000,
    degradedPollMs = 30_000,
    refetchOnFocus = true,
  } = opts;

  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | null>(null);
  const [isLoading, setIsLoading] = useState(enabled);

  const fetcherRef = useRef(opts.fetcher);
  const eventsRef = useRef(opts.events);
  useEffect(() => {
    fetcherRef.current = opts.fetcher;
    eventsRef.current = opts.events;
  });

  const dataRef = useRef<T | undefined>(undefined);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const fetchSeq = useRef(0);
  const mountedRef = useRef(true);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, []);

  const runFetch = useCallback(async (): Promise<void> => {
    if (!enabledRef.current) return;
    const seq = ++fetchSeq.current;
    try {
      const result = await fetcherRef.current();
      if (!mountedRef.current || seq !== fetchSeq.current) return;
      dataRef.current = result;
      setData(result);
      setError(null);
    } catch (err) {
      if (!mountedRef.current || seq !== fetchSeq.current) return;
      setError(
        err instanceof ApiError
          ? err
          : new ApiError(0, undefined, err instanceof Error ? err.message : 'Request failed'),
      );
    } finally {
      if (mountedRef.current && seq === fetchSeq.current) {
        setIsLoading(false);
      }
    }
  }, []);

  const scheduleDebouncedRefetch = useCallback(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      debounceTimer.current = null;
      void runFetch();
    }, REFETCH_DEBOUNCE_MS);
  }, [runFetch]);

  // Fetch on mount / enable / deps change. Data resets to undefined
  // (= loading) so `=== undefined` checks keep their meaning.
  useEffect(() => {
    dataRef.current = undefined;
    setData(undefined);
    setError(null);
    if (!enabled) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    void runFetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, runFetch, ...deps]);

  // Event subscriptions. Keyed on the type list; handlers read config from refs.
  const eventTypesKey = useMemo(
    () => (opts.events ?? []).map((event) => event.type).join('|'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [(opts.events ?? []).map((event) => event.type).join('|')],
  );

  useEffect(() => {
    if (!enabled) return;

    const types = eventTypesKey ? eventTypesKey.split('|') : [];
    const unsubscribes = types.map((type, index) =>
      subscribeAppEvents(type, (event) => {
        if (event.type === '$reconnected') {
          // Every subscription receives the synthetic event; refetch once.
          if (index === 0) void runFetch();
          return;
        }
        const config = (eventsRef.current ?? []).find((c) => c.type === event.type);
        if (!config) return;
        const outcome = config.apply ? config.apply(event.data, dataRef.current) : 'refetch';
        if (outcome === 'refetch') {
          scheduleDebouncedRefetch();
        } else {
          dataRef.current = outcome;
          setData(outcome);
          setIsLoading(false);
        }
      }),
    );

    // With no configured events, still refetch on reconnect.
    if (types.length === 0) {
      unsubscribes.push(
        subscribeAppEvents('$reconnected', () => {
          void runFetch();
        }),
      );
    }

    return () => unsubscribes.forEach((unsubscribe) => unsubscribe());
  }, [enabled, eventTypesKey, runFetch, scheduleDebouncedRefetch]);

  // Refetch on focus / tab becoming visible.
  useEffect(() => {
    if (!enabled || !refetchOnFocus) return;

    const onFocus = () => {
      void runFetch();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void runFetch();
    };

    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [enabled, refetchOnFocus, runFetch]);

  // Backstop poll: slow while the channel is live, faster while degraded.
  const channelState = useEventChannelState();
  useEffect(() => {
    if (!enabled) return;
    const intervalMs = channelState === 'down' ? degradedPollMs : backstopMs;
    const timer = setInterval(() => {
      void runFetch();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [enabled, channelState, backstopMs, degradedPollMs, runFetch]);

  const refetch = useCallback(() => runFetch(), [runFetch]);

  return { data, error, isLoading, refetch };
}
