import { v } from "convex/values";

import type { Id } from "./_generated/dataModel";
import { internal } from "./_generated/api";
import { mutation, query } from "./_generated/server";
import {
  buildUntitledThreadTitle,
  getChatEntitledUser,
  getActiveGenerationForThread,
  getGenerationByClientRequestId,
  getOwnedGenerationOrThrow,
  getOwnedThreadOrThrow,
  requireChatEntitledUser,
} from "./chatModel";

export const listThreads = query({
  args: {},
  handler: async (ctx) => {
    const entitled = await getChatEntitledUser(ctx);
    if (!entitled) return null;

    return ctx.db
      .query("chatThreads")
      .withIndex("by_user_updated", (q) => q.eq("userId", entitled.identity.userId))
      .order("desc")
      .collect()
      .then((threads) => threads.filter((thread) => thread.archivedAt === undefined));
  },
});

export const getThread = query({
  args: {
    threadId: v.id("chatThreads"),
  },
  handler: async (ctx, args) => {
    const { identity } = await requireChatEntitledUser(ctx);
    return getOwnedThreadOrThrow(ctx.db, args.threadId, identity.userId);
  },
});

export const listMessages = query({
  args: {
    threadId: v.id("chatThreads"),
  },
  handler: async (ctx, args) => {
    const entitled = await getChatEntitledUser(ctx);
    if (!entitled) return null;
    await getOwnedThreadOrThrow(ctx.db, args.threadId, entitled.identity.userId);

    return ctx.db
      .query("chatMessages")
      .withIndex("by_thread_created", (q) => q.eq("threadId", args.threadId))
      .collect();
  },
});

export const getActiveGeneration = query({
  args: {
    threadId: v.id("chatThreads"),
  },
  handler: async (ctx, args) => {
    const entitled = await getChatEntitledUser(ctx);
    if (!entitled) return null;
    await getOwnedThreadOrThrow(ctx.db, args.threadId, entitled.identity.userId);
    return getActiveGenerationForThread(ctx.db, args.threadId);
  },
});

export const sendMessage = mutation({
  args: {
    threadId: v.optional(v.id("chatThreads")),
    clientRequestId: v.string(),
    content: v.string(),
  },
  handler: async (ctx, args) => {
    const { identity } = await requireChatEntitledUser(ctx);
    const clientRequestId = args.clientRequestId.trim();
    const content = args.content.trim();

    if (!clientRequestId) {
      throw new Error("clientRequestId is required");
    }

    if (!content) {
      throw new Error("Message content is required");
    }

    const existingGeneration = await getGenerationByClientRequestId(
      ctx.db,
      identity.userId,
      clientRequestId,
    );
    if (existingGeneration) {
      return {
        threadId: existingGeneration.threadId,
        userMessageId: existingGeneration.userMessageId,
        assistantMessageId: existingGeneration.assistantMessageId,
        generationId: existingGeneration._id,
        createdThread: false,
      };
    }

    const now = Date.now();
    let threadId: Id<"chatThreads">;
    let createdThread = false;

    if (args.threadId) {
      const thread = await getOwnedThreadOrThrow(ctx.db, args.threadId, identity.userId);
      threadId = thread._id;
    } else {
      createdThread = true;
      threadId = await ctx.db.insert("chatThreads", {
        userId: identity.userId,
        title: buildUntitledThreadTitle(now),
        createdAt: now,
        updatedAt: now,
        lastMessageAt: now,
      });
    }

    const activeGeneration = await getActiveGenerationForThread(ctx.db, threadId);
    if (activeGeneration) {
      throw new Error("Thread already has an active generation");
    }

    const userMessageId = await ctx.db.insert("chatMessages", {
      threadId,
      userId: identity.userId,
      role: "user",
      content,
      status: "completed",
      createdAt: now,
      updatedAt: now,
      completedAt: now,
    });

    const assistantMessageId = await ctx.db.insert("chatMessages", {
      threadId,
      userId: identity.userId,
      role: "assistant",
      content: "",
      status: "queued",
      chunkSequence: 0,
      createdAt: now,
      updatedAt: now,
    });

    const generationId = await ctx.db.insert("chatGenerations", {
      threadId,
      userId: identity.userId,
      userMessageId,
      assistantMessageId,
      clientRequestId,
      status: "queued",
      provider: "",
      model: "",
      cancelRequested: false,
      createdAt: now,
      updatedAt: now,
    });

    await ctx.db.patch(threadId, {
      updatedAt: now,
      lastMessageAt: now,
    });

    await ctx.scheduler.runAfter(0, internal.chatInternal.kickoffBackendGeneration, {
      generationId,
    });

    return {
      threadId,
      userMessageId,
      assistantMessageId,
      generationId,
      createdThread,
    };
  },
});

export const requestCancel = mutation({
  args: {
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args) => {
    const { identity } = await requireChatEntitledUser(ctx);
    const generation = await getOwnedGenerationOrThrow(
      ctx.db,
      args.generationId,
      identity.userId,
    );

    if (generation.status === "completed" || generation.status === "failed" || generation.status === "cancelled") {
      return { success: false };
    }

    if (generation.cancelRequested) {
      return { success: true };
    }

    await ctx.db.patch(generation._id, {
      cancelRequested: true,
      updatedAt: Date.now(),
    });

    return { success: true };
  },
});
