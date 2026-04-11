import { v } from "convex/values";

import type { Id } from "./_generated/dataModel";
import { internal } from "./_generated/api";
import {
  type ActionCtx,
  internalAction,
  internalMutation,
  internalQuery,
} from "./_generated/server";
import {
  getGenerationById,
  getMessageById,
  getThreadById,
  isTerminalStatus,
} from "./chatModel";

const CHAT_STALE_AFTER_MS = (() => {
  const raw = Number(process.env.CHAT_STALE_AFTER_SECONDS ?? "120");
  if (!Number.isFinite(raw) || raw <= 0) {
    return 120_000;
  }
  return Math.trunc(raw * 1000);
})();

function summarizeError(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object") {
    return fallback;
  }

  const detail = "detail" in payload && typeof payload.detail === "string"
    ? payload.detail
    : undefined;
  const error = "error" in payload && typeof payload.error === "string"
    ? payload.error
    : undefined;

  return detail ?? error ?? fallback;
}

async function failIfStillQueued(
  ctx: ActionCtx,
  generationId: Id<"chatGenerations">,
  errorCode: string,
  errorMessage: string,
) {
  const context = await ctx.runQuery(internal.chatInternal.getGenerationContext, {
    generationId,
  });

  if (!context || context.generation.status !== "queued") {
    return { skipped: true };
  }

  return ctx.runMutation(internal.chatInternal.markGenerationFailed, {
    generationId,
    errorCode,
    errorMessage,
    completedAt: Date.now(),
  });
}

function normalizeTransitionError(message: string) {
  return message.replace(/\s+/g, " ").trim();
}

export const getGenerationContext = internalQuery({
  args: {
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      return null;
    }

    const [thread, userMessage, assistantMessage, transcript] = await Promise.all([
      getThreadById(ctx.db, generation.threadId),
      getMessageById(ctx.db, generation.userMessageId),
      getMessageById(ctx.db, generation.assistantMessageId),
      ctx.db
        .query("chatMessages")
        .withIndex("by_thread_created", (q) => q.eq("threadId", generation.threadId))
        .collect(),
    ]);

    if (!thread || !userMessage || !assistantMessage) {
      throw new Error("Generation context is missing linked records");
    }

    transcript.sort((a, b) => a.createdAt - b.createdAt);

    return {
      generation,
      thread,
      userId: generation.userId,
      userMessage,
      assistantMessage,
      transcript,
    };
  },
});

export const getGenerationCancelState = internalQuery({
  args: {
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    return {
      cancelRequested: generation?.cancelRequested ?? false,
    };
  },
});

export const markGenerationStreaming = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    startedAt: v.number(),
    provider: v.optional(v.string()),
    model: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    if (generation.status === "streaming") {
      return {
        accepted: false,
        status: generation.status,
        generation,
      };
    }

    if (generation.status !== "queued") {
      return {
        accepted: false,
        status: generation.status,
        generation,
      };
    }

    await ctx.db.patch(generation._id, {
      status: "streaming",
      activity: "thinking",
      startedAt: generation.startedAt ?? args.startedAt,
      updatedAt: args.startedAt,
      provider: args.provider?.trim() || generation.provider,
      model: args.model?.trim() || generation.model,
    });

    return {
      accepted: true,
      status: "streaming" as const,
      generation: await ctx.db.get(generation._id),
    };
  },
});

export const heartbeatGeneration = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    updatedAt: v.number(),
    lastTextAt: v.optional(v.number()),
    activity: v.optional(
      v.union(
        v.literal("thinking"),
        v.literal("streaming_text"),
        v.literal("tool_running"),
        v.literal("post_tool_reasoning"),
      ),
    ),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    if (isTerminalStatus(generation.status)) {
      throw new Error(`Cannot patch terminal generation ${generation.status}`);
    }

    await ctx.db.patch(generation._id, {
      status: "streaming",
      activity: args.activity ?? generation.activity ?? "thinking",
      updatedAt: args.updatedAt,
      lastTextAt: args.lastTextAt ?? generation.lastTextAt,
    });

    return ctx.db.get(generation._id);
  },
});

export const markGenerationCompleted = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    content: v.string(),
    completedAt: v.number(),
    providerMessageId: v.optional(v.string()),
    usage: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    if (generation.status === "completed") {
      return generation;
    }

    if (generation.status === "failed" || generation.status === "cancelled") {
      return generation;
    }

    const assistantMessage = await getMessageById(ctx.db, generation.assistantMessageId);
    if (!assistantMessage) {
      throw new Error("Assistant message not found");
    }

    await ctx.db.patch(assistantMessage._id, {
      content: args.content,
      status: "completed",
      providerMessageId: args.providerMessageId ?? assistantMessage.providerMessageId,
      error: undefined,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
    });

    await ctx.db.patch(generation._id, {
      status: "completed",
      activity: undefined,
      providerMessageId: args.providerMessageId ?? generation.providerMessageId,
      usage: args.usage ?? generation.usage,
      errorCode: undefined,
      errorMessage: undefined,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
      lastTextAt: args.content ? args.completedAt : generation.lastTextAt,
    });

    const thread = await getThreadById(ctx.db, generation.threadId);
    if (thread) {
      await ctx.db.patch(thread._id, {
        updatedAt: args.completedAt,
        lastMessageAt: args.completedAt,
      });
    }

    return ctx.db.get(generation._id);
  },
});

export const markGenerationFailed = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    content: v.optional(v.string()),
    errorCode: v.string(),
    errorMessage: v.string(),
    completedAt: v.number(),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    if (generation.status === "failed") {
      return generation;
    }

    if (generation.status === "completed" || generation.status === "cancelled") {
      return generation;
    }

    const assistantMessage = await getMessageById(ctx.db, generation.assistantMessageId);
    if (!assistantMessage) {
      throw new Error("Assistant message not found");
    }

    await ctx.db.patch(assistantMessage._id, {
      content: args.content ?? assistantMessage.content,
      status: "failed",
      error: args.errorMessage,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
    });

    await ctx.db.patch(generation._id, {
      status: "failed",
      activity: undefined,
      errorCode: args.errorCode,
      errorMessage: args.errorMessage,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
      lastTextAt: args.content ? args.completedAt : generation.lastTextAt,
    });

    return ctx.db.get(generation._id);
  },
});

export const markGenerationCancelled = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    content: v.optional(v.string()),
    completedAt: v.number(),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    if (generation.status === "cancelled") {
      return generation;
    }

    if (generation.status === "completed" || generation.status === "failed") {
      return generation;
    }

    const assistantMessage = await getMessageById(ctx.db, generation.assistantMessageId);
    if (!assistantMessage) {
      throw new Error("Assistant message not found");
    }

    await ctx.db.patch(assistantMessage._id, {
      content: args.content ?? assistantMessage.content,
      status: "cancelled",
      error: undefined,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
    });

    await ctx.db.patch(generation._id, {
      status: "cancelled",
      activity: undefined,
      errorCode: undefined,
      errorMessage: undefined,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
      lastTextAt: args.content ? args.completedAt : generation.lastTextAt,
    });

    return ctx.db.get(generation._id);
  },
});

export const failStaleGenerations = internalMutation({
  args: {},
  handler: async (ctx) => {
    const now = Date.now();
    const cutoff = now - CHAT_STALE_AFTER_MS;
    const staleQueued = await ctx.db
      .query("chatGenerations")
      .withIndex("by_status_updated", (q) => q.eq("status", "queued"))
      .collect();
    const staleStreaming = await ctx.db
      .query("chatGenerations")
      .withIndex("by_status_updated", (q) => q.eq("status", "streaming"))
      .collect();

    const stale = [...staleQueued, ...staleStreaming].filter(
      (generation) => generation.updatedAt < cutoff,
    );

    let failed = 0;
    for (const generation of stale) {
      const assistantMessage = await getMessageById(ctx.db, generation.assistantMessageId);
      if (!assistantMessage) {
        continue;
      }

      await ctx.db.patch(assistantMessage._id, {
        status: "failed",
        error: "Generation timed out waiting for backend progress",
        updatedAt: now,
        completedAt: now,
      });

      await ctx.db.patch(generation._id, {
        status: "failed",
        activity: undefined,
        errorCode: "stale_generation",
        errorMessage: "Generation timed out waiting for backend progress",
        updatedAt: now,
        completedAt: now,
      });
      failed += 1;
    }

    return { failed };
  },
});

export const kickoffBackendGeneration = internalAction({
  args: {
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args) => {
    const context = await ctx.runQuery(internal.chatInternal.getGenerationContext, {
      generationId: args.generationId,
    });

    if (!context) {
      return { skipped: true, reason: "generation_not_found" };
    }

    if (context.generation.status !== "queued") {
      return { skipped: true, reason: "generation_not_queued" };
    }

    const backendUrl = process.env.BACKEND_URL;
    const internalSecret = process.env.CHAT_INTERNAL_SECRET;

    if (!backendUrl || !internalSecret) {
      await failIfStillQueued(
        ctx,
        args.generationId,
        "chat_not_configured",
        "Convex backend bridge is missing BACKEND_URL or CHAT_INTERNAL_SECRET",
      );
      return { skipped: false, reason: "bridge_not_configured" };
    }

    try {
      const response = await fetch(`${backendUrl.replace(/\/$/, "")}/api/internal/chat/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Internal-Chat-Secret": internalSecret,
        },
        body: JSON.stringify({ generationId: args.generationId }),
      });

      if (response.ok) {
        return { skipped: false, status: response.status };
      }

      let payload: unknown = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }

      await failIfStillQueued(
        ctx,
        args.generationId,
        "backend_generation_failed",
        summarizeError(payload, `Backend generation request failed with HTTP ${response.status}`),
      );
      return { skipped: false, status: response.status };
    } catch (error) {
      await failIfStillQueued(
        ctx,
        args.generationId,
        "backend_generation_failed",
        error instanceof Error ? error.message : "Backend generation request failed",
      );
      return { skipped: false, reason: "network_error" };
    }
  },
});
