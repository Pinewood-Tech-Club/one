'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { useQuery, useMutation } from 'convex/react';
import { api } from '../../../../convex/_generated/api';
import type { Id } from '../../../../convex/_generated/dataModel';
import { useConvexAuthReady } from '@/components/ConvexClientProvider';

// ── Types ─────────────────────────────────────────────────────────────────────

type StreamingState = {
  content: string;
  status: string;
  activity: string | null;
  generationId: Id<'chatGenerations'>;
} | null;

function normalizePathname(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith('/')) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

function getChatUrl(threadId: Id<'chatThreads'> | null): string {
  return threadId ? `/chat/#${threadId}` : '/chat/';
}

// ── SSE parsing hook ──────────────────────────────────────────────────────────

function useSSEStream(
  generationId: Id<'chatGenerations'> | null,
  onSnapshot: (data: Record<string, unknown>) => void,
  onDelta: (data: Record<string, unknown>) => void,
  onTerminal: (data: Record<string, unknown>) => void,
) {
  useEffect(() => {
    if (!generationId) return;

    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? '';
    const url = `${backendUrl}/api/chat/generations/${generationId}/events`;
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(url, {
          credentials: 'include',
          signal: controller.signal,
          headers: { Accept: 'text/event-stream' },
        });

        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (line.startsWith('event:')) {
              currentEvent = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              try {
                const payload = JSON.parse(line.slice(5).trim());
                if (currentEvent === 'snapshot') onSnapshot(payload);
                else if (currentEvent === 'delta') onDelta(payload);
                else if (currentEvent === 'terminal') {
                  onTerminal(payload);
                  return;
                }
              } catch {
                // malformed JSON — ignore
              }
              currentEvent = '';
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== 'AbortError') {
          console.error('[SSE] Error:', err);
        }
      }
    })();

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generationId]);
}

// ── Thread sidebar ────────────────────────────────────────────────────────────

function ThreadSidebar({
  threads,
  selectedId,
  onSelect,
  onNewChat,
}: {
  threads: Array<{ _id: Id<'chatThreads'>; title: string; lastMessageAt: number }> | undefined;
  selectedId: Id<'chatThreads'> | null;
  onSelect: (id: Id<'chatThreads'>) => void;
  onNewChat: () => void;
}) {
  return (
    <div className="flex flex-col w-64 shrink-0 border-r border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 h-full">
      <div className="p-3 border-b border-zinc-200 dark:border-zinc-700">
        <button
          onClick={onNewChat}
          className="w-full py-2 px-3 rounded-lg bg-green-800 hover:bg-green-700 text-white text-sm font-medium transition-colors"
        >
          + New Chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        {threads === undefined && (
          <div className="px-3 py-2 text-sm text-zinc-500 dark:text-zinc-400 animate-pulse">
            Loading threads...
          </div>
        )}
        {threads?.length === 0 && (
          <div className="px-3 py-2 text-sm text-zinc-500 dark:text-zinc-400">
            No conversations yet
          </div>
        )}
        {threads?.map((thread) => (
          <button
            key={thread._id}
            onClick={() => onSelect(thread._id)}
            className={`w-full text-left px-3 py-2 mx-1 rounded-lg text-sm transition-colors truncate ${
              selectedId === thread._id
                ? 'bg-green-800 text-white'
                : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-800'
            }`}
          >
            {thread.title}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Status indicator ──────────────────────────────────────────────────────────

function StatusPill({ activity, status }: { activity: string | null; status: string }) {
  if (status === 'queued') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
        <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-pulse" />
        Waiting...
      </span>
    );
  }
  if (activity === 'thinking' || activity === 'post_tool_reasoning') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-violet-600 dark:text-violet-400 font-medium">
        <span className="flex gap-0.5">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="w-1 h-1 rounded-full bg-violet-500 animate-bounce"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </span>
        Thinking...
      </span>
    );
  }
  return null;
}

// ── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({
  role,
  content,
  streaming,
  activity,
  status,
}: {
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  activity?: string | null;
  status?: string;
}) {
  const isUser = role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? 'bg-green-800 text-white rounded-br-sm'
            : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 rounded-bl-sm'
        }`}
      >
        {content}
        {streaming && status === 'streaming' && activity === 'streaming_text' && (
          <span className="inline-block w-0.5 h-3.5 bg-current ml-0.5 animate-pulse align-middle" />
        )}
        {streaming && (!content || activity !== 'streaming_text') && (
          <StatusPill activity={activity ?? null} status={status ?? 'queued'} />
        )}
      </div>
    </div>
  );
}

// ── Main chat page ────────────────────────────────────────────────────────────

export default function ChatPage() {
  const pathname = usePathname();
  const isChatRoute = normalizePathname(pathname) === '/chat';
  const [selectedThreadId, setSelectedThreadId] = useState<Id<'chatThreads'> | null>(null);
  const [hashInitialized, setHashInitialized] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [streaming, setStreaming] = useState<StreamingState>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const authReady = useConvexAuthReady();
  const threads = useQuery(api.chat.listThreads, authReady ? {} : 'skip');
  const messages = useQuery(
    api.chat.listMessages,
    authReady && selectedThreadId ? { threadId: selectedThreadId } : 'skip',
  );
  const activeGeneration = useQuery(
    api.chat.getActiveGeneration,
    authReady && selectedThreadId ? { threadId: selectedThreadId } : 'skip',
  );

  const sendMessageMutation = useMutation(api.chat.sendMessage);
  const requestCancelMutation = useMutation(api.chat.requestCancel);

  // SSE handlers (stable refs to avoid restarting stream on re-render)
  const onSnapshot = useCallback((data: Record<string, unknown>) => {
    setStreaming((prev) =>
      prev
        ? {
            ...prev,
            content: (data.content as string) ?? prev.content,
            status: (data.status as string) ?? prev.status,
            activity: (data.activity as string | null) ?? prev.activity,
          }
        : null,
    );
  }, []);

  const onDelta = useCallback((data: Record<string, unknown>) => {
    setStreaming((prev) =>
      prev
        ? {
            ...prev,
            content: prev.content + ((data.delta as string) ?? ''),
            status: (data.status as string) ?? 'streaming',
            activity: 'streaming_text',
          }
        : null,
    );
  }, []);

  const onTerminal = useCallback((data: Record<string, unknown>) => {
    setStreaming(null);
    if (data.status === 'failed') {
      console.error('[Chat] Generation failed:', data.errorMessage);
    }
  }, []);

  useSSEStream(streaming?.generationId ?? null, onSnapshot, onDelta, onTerminal);

  // Bootstrap streaming state from Convex if we arrive on a thread mid-generation
  // (e.g. opening on another device while a generation is in progress)
  useEffect(() => {
    if (!activeGeneration) return;
    if (streaming) {
      // Already tracking — just sync activity/status from heartbeats
      setStreaming((prev) =>
        prev
          ? {
              ...prev,
              activity: (activeGeneration.activity as string | null) ?? prev.activity,
              status: activeGeneration.status,
            }
          : null,
      );
    } else {
      // Not tracking yet — connect to the SSE stream for this generation
      setStreaming({
        content: '',
        status: activeGeneration.status,
        activity: (activeGeneration.activity as string | null) ?? null,
        generationId: activeGeneration._id,
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGeneration?._id, activeGeneration?.activity, activeGeneration?.status]);

  // Clear streaming when Convex says generation is gone
  useEffect(() => {
    if (activeGeneration === null && streaming) {
      setStreaming(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGeneration]);

  // Read hash on mount to restore selected thread
  useEffect(() => {
    if (normalizePathname(window.location.pathname) === '/chat') {
      const hash = window.location.hash.slice(1);
      if (hash) setSelectedThreadId(hash as Id<'chatThreads'>);
    }

    setHashInitialized(true);
  }, []);

  // Sync selected thread to URL hash
  useEffect(() => {
    if (!hashInitialized || !isChatRoute) return;

    window.history.replaceState(null, '', getChatUrl(selectedThreadId));
  }, [hashInitialized, isChatRoute, selectedThreadId]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming?.content]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [inputValue]);

  const handleSend = async () => {
    const content = inputValue.trim();
    if (!content || isSending) return;

    setIsSending(true);
    setInputValue('');

    try {
      const clientRequestId = crypto.randomUUID();
      const result = await sendMessageMutation({
        threadId: selectedThreadId ?? undefined,
        clientRequestId,
        content,
      });

      setSelectedThreadId(result.threadId);
      setStreaming({
        content: '',
        status: 'queued',
        activity: null,
        generationId: result.generationId,
      });
    } catch (err) {
      console.error('[Chat] sendMessage failed:', err);
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewChat = () => {
    setSelectedThreadId(null);
    setStreaming(null);
    setInputValue('');
  };

  // Skip the queued/streaming assistant message from Convex while we have
  // a live streaming bubble — avoids duplication
  const visibleMessages = messages?.filter((msg) => {
    if (streaming && msg.role === 'assistant' && msg.status !== 'completed') return false;
    return true;
  });

  const isGenerating = !!streaming;
  const canCancel =
    streaming && activeGeneration && !activeGeneration.cancelRequested;

  return (
    <div className="flex h-screen pt-[56px] bg-white dark:bg-zinc-950 text-black dark:text-white">
      {/* Sidebar */}
      <ThreadSidebar
        threads={threads}
        selectedId={selectedThreadId}
        onSelect={(id) => {
          setSelectedThreadId(id);
          setStreaming(null);
        }}
        onNewChat={handleNewChat}
      />

      {/* Main area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Empty state */}
        {!selectedThreadId && !streaming && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-zinc-400 dark:text-zinc-600">
            <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
              />
            </svg>
            <p className="text-sm">Select a conversation or start a new one</p>
          </div>
        )}

        {/* Messages */}
        {(selectedThreadId || streaming) && (
          <div className="flex-1 overflow-y-auto px-4 py-6">
            <div className="max-w-2xl mx-auto">
              {messages === undefined && (
                <div className="text-center text-sm text-zinc-400 animate-pulse py-8">
                  Loading messages...
                </div>
              )}

              {visibleMessages?.map((msg) => (
                <MessageBubble
                  key={msg._id}
                  role={msg.role as 'user' | 'assistant'}
                  content={msg.content}
                  status={msg.status}
                />
              ))}

              {/* Live streaming bubble */}
              {streaming && (
                <MessageBubble
                  role="assistant"
                  content={streaming.content}
                  streaming
                  activity={streaming.activity}
                  status={streaming.status}
                />
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>
        )}

        {/* Input bar */}
        <div className="border-t border-zinc-200 dark:border-zinc-800 px-4 py-3">
          <div className="max-w-2xl mx-auto">
            {canCancel && (
              <div className="flex justify-center mb-2">
                <button
                  onClick={() =>
                    requestCancelMutation({ generationId: streaming.generationId })
                  }
                  className="text-xs text-zinc-500 hover:text-red-500 dark:text-zinc-400 dark:hover:text-red-400 underline transition-colors"
                >
                  Stop generating
                </button>
              </div>
            )}

            <div className="flex items-end gap-2 rounded-2xl border border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 px-3 py-2">
              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Message..."
                rows={1}
                disabled={isSending}
                className="flex-1 resize-none bg-transparent text-sm outline-none placeholder-zinc-400 dark:placeholder-zinc-600 py-1 disabled:opacity-50 min-h-[28px] max-h-[160px]"
              />
              <button
                onClick={handleSend}
                disabled={!inputValue.trim() || isSending || isGenerating}
                className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-green-800 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                aria-label="Send message"
              >
                <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M5 12h14M12 5l7 7-7 7"
                  />
                </svg>
              </button>
            </div>
            <p className="text-center text-xs text-zinc-400 dark:text-zinc-600 mt-2">
              Enter to send · Shift+Enter for newline
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
