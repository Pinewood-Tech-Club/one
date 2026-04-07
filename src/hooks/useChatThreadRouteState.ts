'use client';

import { useCallback } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';

import type { Id } from '../../convex/_generated/dataModel';

export function useChatThreadRouteState() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const threadId = searchParams.get('thread') as Id<'chatThreads'> | null;

  const setThreadId = useCallback((nextThreadId: Id<'chatThreads'> | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (nextThreadId) {
      params.set('thread', nextThreadId);
    } else {
      params.delete('thread');
    }

    const query = params.toString();
    const nextUrl = query ? `${pathname}?${query}` : pathname;
    window.history.replaceState(null, '', nextUrl);
  }, [pathname, searchParams]);

  return { threadId, setThreadId };
}
