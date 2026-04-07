'use client';

import { useMutation } from 'convex/react';
import { useCallback } from 'react';

import type { Id } from '../../convex/_generated/dataModel';
import { api } from '../../convex/_generated/api';

function createClientRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useSendChatMessage() {
  const sendMessage = useMutation(api.chat.sendMessage);

  return useCallback(
    (params: { content: string; threadId?: Id<'chatThreads'> | null; clientRequestId?: string }) =>
      sendMessage({
        content: params.content,
        threadId: params.threadId ?? undefined,
        clientRequestId: params.clientRequestId ?? createClientRequestId(),
      }),
    [sendMessage],
  );
}

export function useRequestChatCancel() {
  return useMutation(api.chat.requestCancel);
}
