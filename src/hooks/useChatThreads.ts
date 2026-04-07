'use client';

import { useQuery } from 'convex/react';

import { api } from '../../convex/_generated/api';

export function useChatThreads() {
  return useQuery(api.chat.listThreads, {});
}
