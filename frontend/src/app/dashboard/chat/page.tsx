'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { ChevronUp, BadgePlus, MessageCircleMore, Check, Plus } from 'lucide-react';
import {
  ApiError,
  cancelGeneration,
  getActiveGeneration,
  getChatMessages,
  getChatThreads,
  sendChatMessage,
} from '@/lib/api';
import type { ChatMessage, ChatStatus, ChatThread, ChatGeneration } from '@/lib/api';
import { useAppEvents } from '@/hooks/useAppEvents';
import { useLiveQuery } from '@/hooks/useLiveQuery';
import type { LiveQueryEventConfig } from '@/hooks/useLiveQuery';
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
  generationId: string;
  // Known for sends from this tab; undefined on resume. Lets the terminal
  // handoff write the final message under its real id.
  assistantMessageId?: string;
  toolCalls: StreamingToolCall[];
} | null;

type StreamingToolCall = {
  sequence: number;
  callId: string;
  toolName: string;
  status: string;
  argumentsText?: string;
  summaryText?: string;
  errorText?: string;
};

const ACTIVE_STATUSES = new Set(['queued', 'streaming']);
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

// Grace period before force-clearing streaming after an app-channel terminal
// event: the token SSE terminal / chat.message.created normally complete the
// handoff first and keep the streamed text on screen.
const TERMINAL_EVENT_GRACE_MS = 1_500;
const SSE_RETRY_DELAY_MS = 1_000;
const SSE_REOPEN_DELAY_MS = 2_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizePathname(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith('/')) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

function getChatUrl(threadId: string | null): string {
  return threadId ? `/chat/#${threadId}` : '/chat/';
}

function truncateThreadName(name: string): string {
  if (name.length <= 36) return name;
  return `${name.slice(0, 16)}…${name.slice(-16)}`;
}

function fetchThreads(): Promise<ChatThread[]> {
  return getChatThreads().then((response) => response.threads);
}

function applyThreadEvent(
  data: Record<string, unknown>,
  prev: ChatThread[] | undefined,
): ChatThread[] | 'refetch' {
  const thread = data.thread as ChatThread | undefined;
  if (!prev || !thread || typeof thread._id !== 'string') return 'refetch';
  const next = prev.filter((t) => t._id !== thread._id);
  next.push(thread);
  next.sort((a, b) => b.updatedAt - a.updatedAt);
  return next;
}

const THREAD_EVENTS: LiveQueryEventConfig<ChatThread[]>[] = [
  { type: 'chat.thread.created', apply: applyThreadEvent },
  { type: 'chat.thread.updated', apply: applyThreadEvent },
];

function useTypewriter(target: string, threadId: string | null, charMs = 50): string {
  const [displayed, setDisplayed] = useState(target);
  const prevRef = useRef(target);
  const prevThreadRef = useRef(threadId);

  useEffect(() => {
    const prev = prevRef.current;
    const prevThread = prevThreadRef.current;
    prevRef.current = target;
    prevThreadRef.current = threadId;

    if (prev === target) return;

    // Thread switched — snap immediately, no animation
    if (prevThread !== threadId) {
      setDisplayed(target);
      return;
    }

    let current = prev;
    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      if (current.length > 0) {
        current = current.slice(0, -1);
        setDisplayed(current);
        setTimeout(tick, charMs);
      } else {
        const typeIn = () => {
          if (cancelled) return;
          if (current.length < target.length) {
            current = target.slice(0, current.length + 1);
            setDisplayed(current);
            setTimeout(typeIn, charMs);
          }
        };
        typeIn();
      }
    };

    setTimeout(tick, charMs);
    return () => { cancelled = true; };
  }, [target, threadId, charMs]);

  return displayed;
}

// ── SSE parsing hook ──────────────────────────────────────────────────────────

function useSSEStream(
  generationId: string | null,
  attempt: number,
  onSnapshot: (data: Record<string, unknown>) => void,
  onDelta: (data: Record<string, unknown>) => void,
  onToolCall: (data: Record<string, unknown>) => void,
  onTerminal: (data: Record<string, unknown>) => void,
  onStreamDead: () => void,
) {
  const onStreamDeadRef = useRef(onStreamDead);
  useEffect(() => {
    onStreamDeadRef.current = onStreamDead;
  });

  useEffect(() => {
    if (!generationId) return;

    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? '';
    const url = `${backendUrl}/api/chat/generations/${generationId}/events`;
    const controller = new AbortController();
    let lastEventId: string | null = null;
    let terminated = false;

    const consume = async (useLastEventId: boolean): Promise<void> => {
      const res = await fetch(url, {
        credentials: 'include',
        signal: controller.signal,
        headers: {
          Accept: 'text/event-stream',
          ...(useLastEventId && lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
        },
      });

      if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentId = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) return;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('id:')) {
            currentId = line.slice(3).trim();
          } else if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            if (currentId) lastEventId = currentId;
            try {
              const payload = JSON.parse(line.slice(5).trim());
              if (currentEvent === 'snapshot') onSnapshot(payload);
              else if (currentEvent === 'delta') onDelta(payload);
              else if (currentEvent === 'tool_call') onToolCall(payload);
              else if (currentEvent === 'terminal') {
                terminated = true;
                onTerminal(payload);
                return;
              }
            } catch {
              // malformed JSON — ignore
            }
            currentEvent = '';
            currentId = '';
          }
        }
      }
    };

    (async () => {
      // Initial attempt plus one Last-Event-ID replay retry on transport
      // error; if both fail without a terminal event, hand off to the page
      // (re-fetch active-generation and reopen with a fresh snapshot).
      for (let i = 0; i < 2 && !terminated; i++) {
        try {
          if (i > 0) await sleep(SSE_RETRY_DELAY_MS);
          if (controller.signal.aborted) return;
          await consume(i > 0);
          if (terminated) return;
          // Stream ended without a terminal event — treat as a drop.
        } catch (err: unknown) {
          if (controller.signal.aborted) return;
          if (err instanceof Error && err.name === 'AbortError') return;
          console.error('[SSE] Error:', err);
        }
      }
      if (!terminated && !controller.signal.aborted) onStreamDeadRef.current();
    })();

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generationId, attempt]);
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

function ToolActivityList({ toolCalls }: { toolCalls: StreamingToolCall[] }) {
  if (!toolCalls.length) return null;

  return (
    <div className="w-full rounded-2xl border border-zinc-200 bg-zinc-50 px-4 py-3 text-left">
      <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
        Tool activity
      </div>
      <div className="mt-2 space-y-2">
        {toolCalls.map((toolCall) => {
          const detail =
            toolCall.status === 'completed'
              ? toolCall.summaryText
              : toolCall.status === 'failed'
                ? toolCall.errorText
                : toolCall.argumentsText;
          return (
            <div key={toolCall.callId} className="rounded-xl bg-white px-3 py-2 border border-zinc-200">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-zinc-900">{toolCall.toolName}</div>
                <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                  {toolCall.status}
                </div>
              </div>
              {detail && (
                <div className="mt-1 text-xs leading-relaxed text-zinc-600 break-words">
                  {detail}
                </div>
              )}
            </div>
          );
        })}
      </div>
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
  threads: ChatThread[] | undefined;
  selectedThreadId: string | null;
  onSelectThread: (id: string) => void;
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
  const animatedLabel = useTypewriter(currentLabel, selectedThreadId ?? null);

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
      className="fixed bottom-4 left-1/2 -translate-x-1/2 w-[840px] max-w-[calc(100%-32px)] bg-[#166534] rounded-[24px] p-[4px] flex flex-col gap-[4px] shadow-lg z-20"
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
                  {animatedLabel}
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
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [hashInitialized, setHashInitialized] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [streaming, setStreaming] = useState<StreamingState>(null);
  const [messages, setMessages] = useState<ChatMessage[] | undefined>(undefined);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [forceNotEntitled, setForceNotEntitled] = useState(false);
  const [sseAttempt, setSseAttempt] = useState(0);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const selectedThreadIdRef = useRef<string | null>(null);
  const streamingRef = useRef<StreamingState>(null);
  // Thread id just created/entered by a send from this tab — the thread-change
  // effect keeps the optimistic messages instead of wiping to a loading state.
  const sentThreadRef = useRef<string | null>(null);

  useEffect(() => {
    selectedThreadIdRef.current = selectedThreadId;
  }, [selectedThreadId]);
  useEffect(() => {
    streamingRef.current = streaming;
  }, [streaming]);

  const {
    data: threads,
    error: threadsError,
    refetch: refetchThreads,
  } = useLiveQuery<ChatThread[]>({
    fetcher: fetchThreads,
    events: THREAD_EVENTS,
  });

  const loadMessages = useCallback(
    async (threadId: string, opts?: { background?: boolean }) => {
      if (!opts?.background) setMessages(undefined);
      try {
        const { messages: fetched } = await getChatMessages(threadId);
        if (selectedThreadIdRef.current !== threadId) return;
        setMessages(fetched);
      } catch (err) {
        if (selectedThreadIdRef.current !== threadId) return;
        if (err instanceof ApiError && err.status === 403) {
          setForceNotEntitled(true);
          return;
        }
        console.error('[Chat] loadMessages failed:', err);
        if (!opts?.background) setMessages([]);
      }
    },
    [],
  );

  // Probe for an in-flight generation and resume streaming from a fresh
  // snapshot. No-ops if the thread changed or this tab already streams.
  const probeActiveGeneration = useCallback(async (threadId: string) => {
    try {
      const { generation } = await getActiveGeneration(threadId);
      if (selectedThreadIdRef.current !== threadId || streamingRef.current) return;
      if (generation && ACTIVE_STATUSES.has(generation.status)) {
        setStreaming({
          content: '',
          status: generation.status,
          activity: generation.activity ?? null,
          generationId: generation._id,
          toolCalls: [],
        });
        setCancelRequested(Boolean(generation.cancelRequested));
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setForceNotEntitled(true);
      } else {
        console.error('[Chat] active-generation fetch failed:', err);
      }
    }
  }, []);

  // ── Token-SSE handlers (rendering pipeline unchanged) ──────────────────────

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

  const onToolCall = useCallback((data: Record<string, unknown>) => {
    setStreaming((prev) => {
      if (!prev) return null;

      const callId = typeof data.callId === 'string' ? data.callId : null;
      const toolName = typeof data.toolName === 'string' ? data.toolName : null;
      if (!callId || !toolName) return prev;

      const nextToolCalls = [...prev.toolCalls];
      const existingIndex = nextToolCalls.findIndex((toolCall) => toolCall.callId === callId);
      const nextEntry: StreamingToolCall = {
        sequence: typeof data.sequence === 'number' ? data.sequence : existingIndex >= 0 ? nextToolCalls[existingIndex].sequence : nextToolCalls.length + 1,
        callId,
        toolName,
        status: typeof data.status === 'string' ? data.status : existingIndex >= 0 ? nextToolCalls[existingIndex].status : 'pending',
        argumentsText:
          typeof data.argumentsText === 'string'
            ? data.argumentsText
            : existingIndex >= 0
              ? nextToolCalls[existingIndex].argumentsText
              : undefined,
        summaryText:
          typeof data.summaryText === 'string'
            ? data.summaryText
            : existingIndex >= 0
              ? nextToolCalls[existingIndex].summaryText
              : undefined,
        errorText:
          typeof data.errorText === 'string'
            ? data.errorText
            : existingIndex >= 0
              ? nextToolCalls[existingIndex].errorText
              : undefined,
      };

      if (existingIndex >= 0) {
        nextToolCalls[existingIndex] = nextEntry;
      } else {
        nextToolCalls.push(nextEntry);
      }
      nextToolCalls.sort((a, b) => a.sequence - b.sequence);

      return {
        ...prev,
        activity:
          nextEntry.status === 'running'
            ? 'tool_running'
            : nextEntry.status === 'completed'
              ? 'post_tool_reasoning'
              : prev.activity,
        toolCalls: nextToolCalls,
      };
    });
  }, []);

  // Terminal handoff: KEEP the streamed text — materialize the final
  // assistant message locally, then reconcile in the background.
  const onTerminal = useCallback(
    (data: Record<string, unknown>) => {
      const current = streamingRef.current;
      setStreaming(null);
      setCancelRequested(false);
      if (data.status === 'failed') {
        console.error('[Chat] Generation failed:', data.errorMessage);
      }
      if (!current) return;

      const content = typeof data.content === 'string' ? data.content : current.content;
      const status: ChatStatus =
        typeof data.status === 'string' && TERMINAL_STATUSES.has(data.status)
          ? (data.status as ChatStatus)
          : 'completed';
      const error = typeof data.errorMessage === 'string' ? data.errorMessage : null;
      const threadId = selectedThreadIdRef.current;
      const finalId = current.assistantMessageId ?? `gen-${current.generationId}`;
      const now = Date.now();

      setMessages((prev) => {
        if (!prev) return prev;
        const idx = prev.findIndex((m) => m._id === finalId);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = {
            ...next[idx],
            content,
            status,
            error,
            updatedAt: now,
            completedAt: now,
          };
          return next;
        }
        // The app channel may already have delivered the real final message.
        const last = prev[prev.length - 1];
        if (
          last &&
          last.role === 'assistant' &&
          TERMINAL_STATUSES.has(last.status) &&
          last.content === content
        ) {
          return prev;
        }
        return [
          ...prev,
          {
            _id: finalId,
            threadId: threadId ?? '',
            role: 'assistant',
            content,
            status,
            error,
            createdAt: now,
            updatedAt: now,
            completedAt: now,
          },
        ];
      });

      // Background reconcile: real ids + the generated thread title
      // (the chat.thread.updated event usually beats this).
      if (threadId) void loadMessages(threadId, { background: true });
      void refetchThreads();
    },
    [loadMessages, refetchThreads],
  );

  // Both token-SSE attempts died without a terminal event: re-fetch the
  // active generation and reopen with a fresh snapshot, or clean up.
  const onStreamDead = useCallback(() => {
    const current = streamingRef.current;
    const threadId = selectedThreadIdRef.current;
    if (!current || !threadId) return;
    void (async () => {
      try {
        const { generation } = await getActiveGeneration(threadId);
        if (streamingRef.current?.generationId !== current.generationId) return;
        if (generation && ACTIVE_STATUSES.has(generation.status)) {
          setCancelRequested(Boolean(generation.cancelRequested));
          await sleep(SSE_REOPEN_DELAY_MS);
          if (streamingRef.current?.generationId === current.generationId) {
            setSseAttempt((n) => n + 1);
          }
        } else {
          setStreaming(null);
          setCancelRequested(false);
          void loadMessages(threadId, { background: true });
        }
      } catch (err) {
        console.error('[Chat] active-generation recheck failed:', err);
      }
    })();
  }, [loadMessages]);

  useSSEStream(
    streaming?.generationId ?? null,
    sseAttempt,
    onSnapshot,
    onDelta,
    onToolCall,
    onTerminal,
    onStreamDead,
  );

  // ── App-channel events ──────────────────────────────────────────────────────

  // Cross-tab message visibility + completion handoff for tabs whose token
  // SSE has not delivered terminal yet. Upserts by _id: the final assistant
  // message shares its id with the queued placeholder already in the list.
  useAppEvents('chat.message.created', (event) => {
    if (event.type === '$reconnected') return;
    const message = event.data.message as ChatMessage | undefined;
    const threadId =
      typeof event.data.threadId === 'string' ? event.data.threadId : message?.threadId;
    if (!message || typeof message._id !== 'string' || !threadId) return;
    if (threadId !== selectedThreadIdRef.current) return;

    const isTerminalAssistant =
      message.role === 'assistant' && TERMINAL_STATUSES.has(message.status);

    setMessages((prev) => {
      if (!prev) return prev;
      const idx = prev.findIndex((m) => m._id === message._id);
      let next: ChatMessage[];
      if (idx >= 0) {
        // Already present by real id (skip-if-_id-present guard) — update in place.
        next = [...prev];
        next[idx] = message;
      } else if (message.role === 'user') {
        // Our own send: the event can arrive before the POST response swaps the
        // optimistic temp id. Adopt the real id on the matching temp bubble in
        // place instead of appending a duplicate.
        const tempIdx = prev.findIndex(
          (m) => m._id.startsWith('temp-') && m.content.trim() === message.content.trim(),
        );
        if (tempIdx >= 0) {
          next = [...prev];
          next[tempIdx] = message;
        } else {
          next = [...prev, message];
        }
      } else {
        next = [...prev, message];
      }
      // The real final message supersedes any locally-materialized one.
      if (isTerminalAssistant) {
        next = next.filter((m) => !m._id.startsWith('gen-'));
      }
      return next;
    });

    if (isTerminalAssistant) {
      const current = streamingRef.current;
      if (
        current &&
        (current.assistantMessageId === undefined ||
          current.assistantMessageId === message._id)
      ) {
        setStreaming(null);
        setCancelRequested(false);
      }
    }
  });

  // Resume-in-other-tab + cross-tab cancel-state sync.
  useAppEvents('chat.generation.updated', (event) => {
    if (event.type === '$reconnected') return;
    const generation = event.data.generation as ChatGeneration | undefined;
    const threadId =
      typeof event.data.threadId === 'string' ? event.data.threadId : generation?.threadId;
    if (!generation || typeof generation._id !== 'string' || !threadId) return;
    if (threadId !== selectedThreadIdRef.current) return;

    const current = streamingRef.current;

    if (ACTIVE_STATUSES.has(generation.status)) {
      if (!current) {
        // Started in another tab — resume; the SSE snapshot fills the content.
        setStreaming({
          content: '',
          status: generation.status,
          activity: generation.activity ?? null,
          generationId: generation._id,
          toolCalls: [],
        });
        setCancelRequested(Boolean(generation.cancelRequested));
      } else if (current.generationId === generation._id) {
        setCancelRequested(Boolean(generation.cancelRequested));
      }
      return;
    }

    // Terminal while this tab still shows streaming: the token SSE terminal /
    // chat.message.created normally complete the handoff (keeping the text);
    // only force-clear if neither has arrived after a grace period.
    if (current && current.generationId === generation._id) {
      window.setTimeout(() => {
        if (streamingRef.current?.generationId !== generation._id) return;
        setStreaming(null);
        setCancelRequested(false);
        if (selectedThreadIdRef.current === threadId) {
          void loadMessages(threadId, { background: true });
        }
      }, TERMINAL_EVENT_GRACE_MS);
    }
  });

  // After an SSE outage, events may have been missed. Resync the selected
  // thread's messages and active-generation state (mirrors the mount/select
  // logic). The typed handlers above early-return on $reconnected; this one
  // owns the recovery. No-ops when no thread is selected.
  useAppEvents('$reconnected', () => {
    const threadId = selectedThreadIdRef.current;
    if (!threadId) return;
    void loadMessages(threadId, { background: true });
    void probeActiveGeneration(threadId);
  });

  // ── Thread selection / hash routing ────────────────────────────────────────

  useEffect(() => {
    if (normalizePathname(window.location.pathname) === '/chat') {
      const hash = window.location.hash.slice(1);
      if (hash) setSelectedThreadId(hash);
    }

    setHashInitialized(true);
  }, []);

  useEffect(() => {
    if (!hashInitialized || !isChatRoute) return;

    window.history.replaceState(null, '', getChatUrl(selectedThreadId));
  }, [hashInitialized, isChatRoute, selectedThreadId]);

  // Load messages + resume any active generation on thread select.
  useEffect(() => {
    if (!hashInitialized) return;
    if (!selectedThreadId) {
      setMessages(undefined);
      return;
    }

    const threadId = selectedThreadId;
    const fromSend = sentThreadRef.current === threadId;
    sentThreadRef.current = null;

    void loadMessages(threadId, { background: fromSend });

    // A send from this tab already holds the streaming state; only probe for
    // an in-flight generation when arriving at the thread some other way.
    if (fromSend) return;
    void probeActiveGeneration(threadId);
  }, [hashInitialized, selectedThreadId, loadMessages, probeActiveGeneration]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming?.content]);

  // ── Actions ─────────────────────────────────────────────────────────────────

  const handleSend = async () => {
    const content = inputValue.trim();
    if (!content || isSending || streaming) return;

    setIsSending(true);
    setInputValue('');

    const tempId = `temp-${crypto.randomUUID()}`;
    const now = Date.now();
    const optimistic: ChatMessage = {
      _id: tempId,
      threadId: selectedThreadId ?? '',
      role: 'user',
      content,
      status: 'completed',
      error: null,
      createdAt: now,
      updatedAt: now,
      completedAt: now,
    };
    setMessages((prev) => (prev ? [...prev, optimistic] : [optimistic]));

    try {
      const result = await sendChatMessage({
        threadId: selectedThreadId ?? undefined,
        clientRequestId: crypto.randomUUID(),
        content,
      });

      // Swap the optimistic id for the real one (the chat.message.created
      // event may have landed the real message already — then just drop temp).
      setMessages((prev) => {
        if (!prev) return prev;
        if (prev.some((m) => m._id === result.userMessageId)) {
          return prev.filter((m) => m._id !== tempId);
        }
        return prev.map((m) =>
          m._id === tempId ? { ...m, _id: result.userMessageId, threadId: result.threadId } : m,
        );
      });

      if (result.threadId !== selectedThreadId) {
        sentThreadRef.current = result.threadId;
      }
      setSelectedThreadId(result.threadId);
      setCancelRequested(false);
      setStreaming({
        content: '',
        status: 'queued',
        activity: null,
        generationId: result.generationId,
        assistantMessageId: result.assistantMessageId,
        toolCalls: [],
      });
    } catch (err) {
      setMessages((prev) => (prev ? prev.filter((m) => m._id !== tempId) : prev));
      setInputValue((prevInput) => prevInput || content);
      if (err instanceof ApiError && err.status === 403) {
        setForceNotEntitled(true);
      } else {
        console.error('[Chat] sendMessage failed:', err);
      }
    } finally {
      setIsSending(false);
    }
  };

  const handleCancel = async () => {
    const current = streaming;
    if (!current || cancelRequested) return;
    setCancelRequested(true);
    try {
      await cancelGeneration(current.generationId);
    } catch (err) {
      console.error('[Chat] cancel failed:', err);
      if (streamingRef.current?.generationId === current.generationId) {
        setCancelRequested(false);
      }
    }
  };

  const handleSelectThread = (id: string) => {
    setSelectedThreadId(id);
    setStreaming(null);
    setCancelRequested(false);
  };

  const handleNewChat = () => {
    setSelectedThreadId(null);
    setStreaming(null);
    setCancelRequested(false);
    setInputValue('');
  };

  const visibleMessages = messages?.filter((msg) => {
    if (streaming && msg.role === 'assistant' && msg.status !== 'completed') return false;
    return true;
  });

  const isGenerating = !!streaming;
  const canCancel = !!streaming && !cancelRequested;

  const hasContent = selectedThreadId || streaming;
  const notEntitled = threadsError?.status === 403 || forceNotEntitled;

  if (notEntitled) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-3 text-zinc-400">
        <MessageCircleMore className="w-12 h-12" strokeWidth={1.5} />
        <p className="text-sm">Chat isn't available for your account yet.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen pt-[72px] pb-[220px] bg-white text-black overflow-x-hidden">
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
          <>
            <ToolActivityList toolCalls={streaming.toolCalls} />
            <AssistantMessage
              content={streaming.content}
              streaming
              activity={streaming.activity}
              status={streaming.status}
            />
          </>
        )}

        <div ref={messagesEndRef} />
      </div>

      {canCancel && (
        <div className="fixed bottom-[200px] left-1/2 -translate-x-1/2 z-30">
          <button
            onClick={handleCancel}
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
