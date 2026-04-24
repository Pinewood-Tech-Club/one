import type { Doc, Id } from "./_generated/dataModel";
import type { DatabaseReader, MutationCtx, QueryCtx } from "./_generated/server";

import { getAuthenticatedUser } from "./auth";

export type ChatThreadId = Id<"chatThreads">;
export type ChatMessageId = Id<"chatMessages">;
export type ChatGenerationId = Id<"chatGenerations">;
export type ChatThread = Doc<"chatThreads">;
export type ChatMessage = Doc<"chatMessages">;
export type ChatGeneration = Doc<"chatGenerations">;
export type ChatStatus = ChatGeneration["status"];

type ChatAccessCtx = QueryCtx | MutationCtx;

const ACTIVE_STATUSES = new Set<ChatStatus>(["queued", "streaming"]);
const TERMINAL_STATUSES = new Set<ChatStatus>(["completed", "failed", "cancelled"]);

export function isActiveStatus(status: ChatStatus) {
  return ACTIVE_STATUSES.has(status);
}

export function isTerminalStatus(status: ChatStatus) {
  return TERMINAL_STATUSES.has(status);
}

export function buildUntitledThreadTitle(_now: number) {
  return 'New chat';
}

export async function getUserRecordByUserId(db: DatabaseReader, userId: string) {
  return db
    .query("users")
    .withIndex("by_user", (q) => q.eq("userId", userId))
    .first();
}

export async function requireChatEntitledUser(ctx: ChatAccessCtx) {
  const identity = await getAuthenticatedUser(ctx);
  const userRecord = await getUserRecordByUserId(ctx.db, identity.userId);

  if (!userRecord) {
    throw new Error("User not found");
  }

  if (userRecord.onboardingStep !== "completed") {
    throw new Error("Chat requires completed onboarding");
  }

  if (userRecord.smartFeaturesConsent?.enabled !== true) {
    throw new Error("Chat requires smart features consent");
  }

  return { identity, userRecord };
}

export async function getChatEntitledUser(ctx: ChatAccessCtx) {
  try {
    return await requireChatEntitledUser(ctx);
  } catch {
    return null;
  }
}

export async function getThreadById(db: DatabaseReader, threadId: ChatThreadId) {
  return db.get(threadId);
}

export async function getMessageById(db: DatabaseReader, messageId: ChatMessageId) {
  return db.get(messageId);
}

export async function getGenerationById(db: DatabaseReader, generationId: ChatGenerationId) {
  return db.get(generationId);
}

export async function getGenerationByClientRequestId(
  db: DatabaseReader,
  userId: string,
  clientRequestId: string,
) {
  return db
    .query("chatGenerations")
    .withIndex("by_user_request", (q) =>
      q.eq("userId", userId).eq("clientRequestId", clientRequestId),
    )
    .first();
}

export async function getOwnedThreadOrThrow(
  db: DatabaseReader,
  threadId: ChatThreadId,
  userId: string,
) {
  const thread = await getThreadById(db, threadId);
  if (!thread || thread.userId !== userId) {
    throw new Error("Thread not found");
  }
  return thread;
}

export async function getOwnedGenerationOrThrow(
  db: DatabaseReader,
  generationId: ChatGenerationId,
  userId: string,
) {
  const generation = await getGenerationById(db, generationId);
  if (!generation || generation.userId !== userId) {
    throw new Error("Generation not found");
  }
  return generation;
}

export async function getActiveGenerationForThread(
  db: DatabaseReader,
  threadId: ChatThreadId,
) {
  const queued = await db
    .query("chatGenerations")
    .withIndex("by_thread_status", (q) =>
      q.eq("threadId", threadId).eq("status", "queued"),
    )
    .collect();
  const streaming = await db
    .query("chatGenerations")
    .withIndex("by_thread_status", (q) =>
      q.eq("threadId", threadId).eq("status", "streaming"),
    )
    .collect();

  const active = [...queued, ...streaming];
  active.sort((a, b) => b.updatedAt - a.updatedAt);
  return active[0] ?? null;
}
