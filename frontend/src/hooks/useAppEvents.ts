'use client';

// Module-singleton SSE reader for GET /api/events. One connection per tab,
// shared by every subscriber; reconnects with capped backoff + Last-Event-ID.

import { useEffect, useRef, useSyncExternalStore } from 'react';

export type AppEvent = {
  id: string;
  type: string;
  data: Record<string, unknown>;
};

export type EventChannelState = 'connecting' | 'open' | 'down';

type Subscriber = {
  type: string;
  handler: (event: AppEvent) => void;
};

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? '';
const BACKOFF_MS = [1_000, 2_000, 5_000, 10_000];
const DOWN_RETRY_MS = 60_000;

const subscribers = new Set<Subscriber>();
const stateListeners = new Set<() => void>();

let channelState: EventChannelState = 'connecting';
let controller: AbortController | null = null;
let running = false;
let lastEventId: string | null = null;
let attempt = 0;
let everConnected = false;

function setChannelState(next: EventChannelState): void {
  if (channelState === next) return;
  channelState = next;
  stateListeners.forEach((listener) => listener());
}

function dispatch(event: AppEvent): void {
  // Synthetic $reconnected goes to every subscriber; real events are filtered.
  for (const sub of Array.from(subscribers)) {
    if (event.type !== '$reconnected' && sub.type !== '*' && sub.type !== event.type) {
      continue;
    }
    try {
      sub.handler(event);
    } catch (err) {
      console.error('[app-events] subscriber error:', err);
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readStream(body: ReadableStream<Uint8Array>): Promise<void> {
  const reader = body.getReader();
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
        try {
          const payload: unknown = JSON.parse(line.slice(5).trim());
          if (currentId) lastEventId = currentId;
          dispatch({
            id: currentId,
            type: currentEvent || 'message',
            data:
              payload && typeof payload === 'object' && !Array.isArray(payload)
                ? (payload as Record<string, unknown>)
                : {},
          });
        } catch {
          // malformed JSON — ignore
        }
        currentEvent = '';
        currentId = '';
      }
    }
  }
}

async function runLoop(): Promise<void> {
  while (subscribers.size > 0) {
    const ctrl = new AbortController();
    controller = ctrl;

    try {
      const response = await fetch(`${BACKEND_URL}/api/events`, {
        credentials: 'include',
        signal: ctrl.signal,
        headers: {
          Accept: 'text/event-stream',
          ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
        },
      });

      if (response.status === 503) {
        setChannelState('down');
        await sleep(DOWN_RETRY_MS);
        continue;
      }

      if (!response.ok || !response.body) {
        throw new Error(`events channel HTTP ${response.status}`);
      }

      setChannelState('open');
      const isReconnect = everConnected;
      everConnected = true;
      attempt = 0;
      if (isReconnect) {
        dispatch({ id: lastEventId ?? '', type: '$reconnected', data: {} });
      }

      await readStream(response.body);
      // Server closed the never-ending stream — treat as a drop and reconnect.
    } catch (err) {
      if (ctrl.signal.aborted) break;
      if (err instanceof Error && err.name !== 'AbortError') {
        console.error('[app-events] connection error:', err);
      }
    } finally {
      if (controller === ctrl) controller = null;
    }

    if (subscribers.size === 0) break;
    setChannelState('connecting');
    const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    await sleep(delay);
  }

  running = false;
  controller = null;
  // A subscriber may have arrived while the loop was winding down.
  if (subscribers.size > 0) ensureConnected();
}

function ensureConnected(): void {
  if (typeof window === 'undefined') return;
  if (running) return;
  running = true;
  setChannelState('connecting');
  void runLoop();
}

export function subscribeAppEvents(
  type: string | '*',
  handler: (event: AppEvent) => void,
): () => void {
  const sub: Subscriber = { type, handler };
  subscribers.add(sub);
  ensureConnected();
  return () => {
    subscribers.delete(sub);
    if (subscribers.size === 0) {
      controller?.abort();
    }
  };
}

export function useAppEvents(type: string | '*', handler: (event: AppEvent) => void): void {
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });

  useEffect(() => {
    return subscribeAppEvents(type, (event) => handlerRef.current(event));
  }, [type]);
}

export function useEventChannelState(): EventChannelState {
  return useSyncExternalStore(
    (listener) => {
      stateListeners.add(listener);
      return () => stateListeners.delete(listener);
    },
    () => channelState,
    () => 'connecting' as EventChannelState,
  );
}
