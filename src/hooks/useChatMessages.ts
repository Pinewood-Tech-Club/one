'use client';

import { useQuery } from 'convex/react';

import type { Id } from '../../convex/_generated/dataModel';
import { api } from '../../convex/_generated/api';

export function useChatMessages(threadId: Id<'chatThreads'> | null | undefined) {
  return useQuery(
    api.chat.listMessages,
    threadId ? { threadId } : 'skip',
  );
}

export function useActiveChatGeneration(threadId: Id<'chatThreads'> | null | undefined) {
  return useQuery(
    api.chat.getActiveGeneration,
    threadId ? { threadId } : 'skip',
  );
}
