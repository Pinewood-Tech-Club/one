'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { useQuery, useMutation } from 'convex/react';
import { ChevronUp, BadgePlus, MessageCircleMore, Check, Plus } from 'lucide-react';
import { api } from '../../../../convex/_generated/api';
import type { Id } from '../../../../convex/_generated/dataModel';
import { useConvexAuthReady } from '@/components/ConvexClientProvider';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

// ── Types ─────────────────────────────────────────────────────────────────────

type StreamingState = {
  content: string;
  status: string;
  activity: string | null;
  generationId: Id<'chatGenerations'>;
} | null;

type Thread = { _id: Id<'chatThreads'>; title: string; lastMessageAt: number };

function normalizePathname(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith('/')) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

function getChatUrl(threadId: Id<'chatThreads'> | null): string {
  return threadId ? `/chat/#${threadId}` : '/chat/';
}

function truncateThreadName(name: string): string {
  if (name.length <= 36) return name;
  return `${name.slice(0, 16)}…${name.slice(-16)}`;
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

// ── Status indicator ──────────────────────────────────────────────────────────

function StatusPill({ activity, status }: { activity: string | null; status: string }) {
  if (status === 'queued') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500">
        <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-pulse" />
        Waiting...
      </span>
    );
  }
  if (activity === 'thinking' || activity === 'post_tool_reasoning') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-violet-600 font-medium">
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

// ── Message renderers ─────────────────────────────────────────────────────────

function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex w-full justify-end">
      <div className="bg-green-800 text-white rounded-[24px] px-4 py-3 max-w-[67%] text-[15px] leading-relaxed whitespace-pre-wrap break-words text-left">
        {content}
      </div>
    </div>
  );
}

const mdComponents: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  // Paragraphs — no extra margin at top, tight gap between
  p: ({ children }) => (
    <p className="mb-3 last:mb-0 leading-relaxed">{children}</p>
  ),
  // Headings
  h1: ({ children }) => (
    <h1 className="text-xl font-semibold mt-5 mb-2 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-semibold mt-4 mb-2 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-semibold mt-3 mb-1 first:mt-0">{children}</h3>
  ),
  // Lists
  ul: ({ children }) => (
    <ul className="list-disc list-outside pl-5 mb-3 space-y-0.5 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-outside pl-5 mb-3 space-y-0.5 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  // Blockquote
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-zinc-300 pl-4 text-zinc-600 italic mb-3 last:mb-0">
      {children}
    </blockquote>
  ),
  // Inline code
  code: ({ children, className }) => {
    // Block code comes from pre > code and has a className like "language-xxx"
    if (className) return <code className={className}>{children}</code>;
    return (
      <code className="bg-zinc-100 text-zinc-800 rounded px-1 py-0.5 text-[13px] font-mono">
        {children}
      </code>
    );
  },
  // Code block wrapper
  pre: ({ children }) => (
    <pre className="bg-zinc-50 border border-zinc-200 rounded-xl px-4 py-3 overflow-x-auto mb-3 last:mb-0 text-[13px] leading-relaxed">
      {children}
    </pre>
  ),
  // Horizontal rule
  hr: () => <hr className="border-zinc-200 my-4" />,
  // Links
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-green-700 underline underline-offset-2 hover:text-green-900">
      {children}
    </a>
  ),
  // Strong / em
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  // Tables (GFM)
  table: ({ children }) => (
    <div className="overflow-x-auto mb-3 last:mb-0">
      <table className="w-full text-sm border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="border-b border-zinc-200">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-zinc-100 last:border-0">{children}</tr>,
  th: ({ children }) => (
    <th className="text-left font-semibold px-3 py-2 first:pl-0 last:pr-0">{children}</th>
  ),
  td: ({ children }) => (
    <td className="px-3 py-2 first:pl-0 last:pr-0 align-top">{children}</td>
  ),
};

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex, rehypeHighlight]}
      components={mdComponents}
    >
      {content}
    </ReactMarkdown>
  );
}

function AssistantMessage({
  content,
  streaming,
  activity,
  status,
}: {
  content: string;
  streaming?: boolean;
  activity?: string | null;
  status?: string;
}) {
  return (
    <div className="w-full text-black text-[15px] text-left">
      {content && <MarkdownContent content={content} />}
      {streaming && status === 'streaming' && activity === 'streaming_text' && (
        <span className="inline-block w-0.5 h-3.5 bg-current ml-0.5 animate-pulse align-middle" />
      )}
      {streaming && (!content || activity !== 'streaming_text') && (
        <StatusPill activity={activity ?? null} status={status ?? 'queued'} />
      )}
    </div>
  );
}

// ── Composer ──────────────────────────────────────────────────────────────────

function Composer({
  threads,
  selectedThreadId,
  onSelectThread,
  onNewChat,
  inputValue,
  setInputValue,
  onSend,
  disabled,
}: {
  threads: Thread[] | undefined;
  selectedThreadId: Id<'chatThreads'> | null;
  onSelectThread: (id: Id<'chatThreads'>) => void;
  onNewChat: () => void;
  inputValue: string;
  setInputValue: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
}) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [inputValue]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (!dropdownRef.current?.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [dropdownOpen]);

  const selectedThread = threads?.find((t) => t._id === selectedThreadId);
  const otherThreads = (threads ?? []).filter((t) => t._id !== selectedThreadId);
  const currentLabel = selectedThread
    ? truncateThreadName(selectedThread.title)
    : 'New chat';

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const showTrigger = !dropdownOpen || !!selectedThread;

  return (
    <div
      ref={dropdownRef}
      className="fixed bottom-4 left-1/2 -translate-x-1/2 w-[840px] max-w-[calc(100vw-32px)] bg-[#166534] rounded-[24px] p-[4px] flex flex-col gap-[4px] shadow-lg z-20"
    >
      {/* Top row: thread pill (morphs hug↔fill) + new-chat button.
          flex-grow on the pill transitions 0 → 1; an invisible spacer
          does the inverse so the + button stays pinned to the right.
          gap-[4px] keeps a fixed breathing room between pill and +. */}
      <div className="flex items-end w-full gap-[4px]">
        <div
          className="bg-white text-[#166534] rounded-tl-[20px] rounded-tr-[20px] overflow-hidden min-w-0 flex flex-col transition-[flex-grow] duration-300 ease-out"
          style={{ flexGrow: dropdownOpen ? 1 : 0, flexShrink: 0, flexBasis: 'auto' }}
        >
          {/* Trigger row wrapped in a grid-rows height animator. Collapses
              to 0 when opening a new-chat state ("fades out to reveal the
              chat selector"). No `contain` here — the trigger's intrinsic
              width must flow up to the pill so it hugs the trigger width
              in the closed state. */}
          <div
            className={`grid transition-[grid-template-rows] duration-300 ease-out ${
              showTrigger ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
            }`}
          >
            <div className="overflow-hidden min-h-0">
              <button
                type="button"
                onClick={() => setDropdownOpen((o) => !o)}
                className={`w-full flex items-center gap-[10px] px-[12px] py-[8px] text-[18px] leading-none whitespace-nowrap hover:bg-black/[0.02] transition-colors ${
                  dropdownOpen && otherThreads.length > 0 ? 'border-b border-[#676767]' : ''
                }`}
              >
                <span className="flex-1 text-left min-w-0 truncate">
                  {currentLabel}
                </span>
                <span className="relative w-4 h-4 shrink-0">
                  <ChevronUp
                    className={`absolute inset-0 w-4 h-4 transition-opacity duration-200 ${
                      dropdownOpen ? 'opacity-0' : 'opacity-100'
                    }`}
                    strokeWidth={2}
                  />
                  {selectedThread && (
                    <Check
                      className={`absolute inset-0 w-4 h-4 transition-opacity duration-200 ${
                        dropdownOpen ? 'opacity-100' : 'opacity-0'
                      }`}
                      strokeWidth={2}
                    />
                  )}
                </span>
              </button>
            </div>
          </div>

          {/* Expanded rows wrapped in a grid-rows height animator.
              `contain: inline-size` isolates the list's intrinsic inline
              size (== 0) from the pill, so hidden rows with long thread
              names can't fatten the closed pill. */}
          <div
            className={`grid transition-[grid-template-rows] duration-300 ease-out ${
              dropdownOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
            }`}
            style={{ contain: 'inline-size' }}
          >
            <div className="overflow-hidden min-h-0 flex flex-col">
              {otherThreads.length === 0 && !selectedThread && (
                <div className="px-[12px] py-[8px] text-[18px] text-[#676767] font-extralight">
                  No conversations yet
                </div>
              )}
              {otherThreads.map((t, idx) => (
                <button
                  key={t._id}
                  type="button"
                  onClick={() => {
                    onSelectThread(t._id);
                    setDropdownOpen(false);
                  }}
                  className={`text-left px-[12px] py-[8px] text-[18px] text-[#676767] font-extralight whitespace-nowrap hover:bg-black/[0.02] truncate ${
                    idx < otherThreads.length - 1 ? 'border-b border-[#676767]' : ''
                  }`}
                >
                  {truncateThreadName(t.title)}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Spacer that fills the row when the pill is hugged. */}
        <div
          className="transition-[flex-grow] duration-300 ease-out"
          style={{ flexGrow: dropdownOpen ? 0 : 1, flexShrink: 1, flexBasis: 0 }}
          aria-hidden
        />

        {/* New chat button — matches the trigger's structure (text-[18px],
            gap-[10px], w-4 h-4 icon) so its box height equals the pill's. */}
        <button
          type="button"
          onClick={onNewChat}
          aria-label="New chat"
          className="bg-white text-[#166534] rounded-tl-[20px] rounded-tr-[20px] px-[12px] py-[8px] flex items-center justify-center transition-colors shrink-0 self-end"
        >
          <Plus className="w-[18px] h-[18px]" strokeWidth={2} />
        </button>
      </div>

      {/* Textarea */}
      <div className="bg-white rounded-bl-[20px] rounded-br-[20px] pt-[10px] pb-[12px] px-[16px] flex items-start">
        <textarea
          ref={textareaRef}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setDropdownOpen(false)}
          onMouseDown={() => setDropdownOpen(false)}
          placeholder="What do you want to know?"
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none bg-transparent text-[18px] text-black placeholder:text-[#676767] outline-none min-h-[26px] max-h-[160px] leading-normal disabled:opacity-50"
        />
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

  useEffect(() => {
    if (!activeGeneration) return;
    if (streaming) {
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
      setStreaming({
        content: '',
        status: activeGeneration.status,
        activity: (activeGeneration.activity as string | null) ?? null,
        generationId: activeGeneration._id,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGeneration?._id, activeGeneration?.activity, activeGeneration?.status]);

  useEffect(() => {
    if (activeGeneration === null && streaming) {
      setStreaming(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGeneration]);

  useEffect(() => {
    if (normalizePathname(window.location.pathname) === '/chat') {
      const hash = window.location.hash.slice(1);
      if (hash) setSelectedThreadId(hash as Id<'chatThreads'>);
    }

    setHashInitialized(true);
  }, []);

  useEffect(() => {
    if (!hashInitialized || !isChatRoute) return;

    window.history.replaceState(null, '', getChatUrl(selectedThreadId));
  }, [hashInitialized, isChatRoute, selectedThreadId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming?.content]);

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

  const handleSelectThread = (id: Id<'chatThreads'>) => {
    setSelectedThreadId(id);
    setStreaming(null);
  };

  const handleNewChat = () => {
    setSelectedThreadId(null);
    setStreaming(null);
    setInputValue('');
  };

  const visibleMessages = messages?.filter((msg) => {
    if (streaming && msg.role === 'assistant' && msg.status !== 'completed') return false;
    return true;
  });

  const isGenerating = !!streaming;
  const canCancel =
    streaming && activeGeneration && !activeGeneration.cancelRequested;

  const hasContent = selectedThreadId || streaming;
  const notEntitled = threads === null;

  if (notEntitled) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-3 text-zinc-400">
        <MessageCircleMore className="w-12 h-12" strokeWidth={1.5} />
        <p className="text-sm">Chat isn't available for your account yet.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen pt-[72px] pb-[220px] bg-white text-black">
      <div className="mx-auto w-full max-w-[840px] px-4 flex flex-col gap-5">
        {!hasContent && (
          <div className="flex flex-col items-center justify-center gap-3 text-zinc-400 pt-32">
            <MessageCircleMore className="w-12 h-12" strokeWidth={1.5} />
            <p className="text-sm">if you're reading this adam forgot to actually write something here lmao</p>
          </div>
        )}

        {hasContent && messages === undefined && (
          <div className="text-center text-sm text-zinc-400 animate-pulse py-8">
            Loading messages...
          </div>
        )}

        {visibleMessages?.map((msg) =>
          msg.role === 'user' ? (
            <UserMessage key={msg._id} content={msg.content} />
          ) : (
            <AssistantMessage key={msg._id} content={msg.content} status={msg.status} />
          ),
        )}

        {streaming && (
          <AssistantMessage
            content={streaming.content}
            streaming
            activity={streaming.activity}
            status={streaming.status}
          />
        )}

        <div ref={messagesEndRef} />
      </div>

      {canCancel && (
        <div className="fixed bottom-[200px] left-1/2 -translate-x-1/2 z-30">
          <button
            onClick={() =>
              requestCancelMutation({ generationId: streaming.generationId })
            }
            className="px-3 py-1.5 rounded-full bg-white border border-zinc-300 text-xs text-zinc-600 hover:text-red-500 hover:border-red-300 shadow-sm transition-colors"
          >
            Stop generating
          </button>
        </div>
      )}

      <Composer
        threads={threads}
        selectedThreadId={selectedThreadId}
        onSelectThread={handleSelectThread}
        onNewChat={handleNewChat}
        inputValue={inputValue}
        setInputValue={setInputValue}
        onSend={handleSend}
        disabled={isSending || isGenerating}
      />
    </div>
  );
}
