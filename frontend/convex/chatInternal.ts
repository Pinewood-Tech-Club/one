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
  getUserRecordByUserId,
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

    const [thread, userMessage, assistantMessage, transcript, userRecord, memberships, toolCalls] = await Promise.all([
      getThreadById(ctx.db, generation.threadId),
      getMessageById(ctx.db, generation.userMessageId),
      getMessageById(ctx.db, generation.assistantMessageId),
      ctx.db
        .query("chatMessages")
        .withIndex("by_thread_created", (q) => q.eq("threadId", generation.threadId))
        .collect(),
      getUserRecordByUserId(ctx.db, generation.userId),
      ctx.db
        .query("schoologyCourseMemberships")
        .withIndex("by_user", (q) => q.eq("userId", generation.userId))
        .collect(),
      ctx.db
        .query("chatToolCalls")
        .withIndex("by_thread_created", (q) => q.eq("threadId", generation.threadId))
        .collect(),
    ]);

    if (!thread || !userMessage || !assistantMessage) {
      throw new Error("Generation context is missing linked records");
    }

    transcript.sort((a, b) => a.createdAt - b.createdAt);
    toolCalls.sort((a, b) => {
      if (a.createdAt !== b.createdAt) {
        return a.createdAt - b.createdAt;
      }
      return a.sequence - b.sequence;
    });

    const courseIds = Array.from(
      new Set(
        memberships
          .filter((membership) => membership.isActive)
          .map((membership) => membership.courseId),
      ),
    );
    const courseRows = await Promise.all(
      courseIds.map((courseId) =>
        ctx.db
          .query("schoologyCourses")
          .withIndex("by_course", (q) => q.eq("courseId", courseId))
          .first(),
      ),
    );
    const courses = courseRows
      .filter((course): course is NonNullable<typeof course> => course !== null)
      .map((course) => ({
        courseId: course.courseId,
        courseTitle:
          typeof course.data?.course_title === "string" && course.data.course_title.trim()
            ? course.data.course_title.trim()
            : typeof course.data?.title === "string" && course.data.title.trim()
              ? course.data.title.trim()
              : course.courseId,
        sectionTitle:
          typeof course.data?.section_title === "string" && course.data.section_title.trim()
            ? course.data.section_title.trim()
            : typeof course.data?.title === "string" && course.data.title.trim()
              ? course.data.title.trim()
              : undefined,
      }));

    return {
      generation,
      thread,
      userId: generation.userId,
      userMessage,
      assistantMessage,
      transcript,
      userRecord: userRecord
        ? {
            userId: userRecord.userId,
            onboardingStep: userRecord.onboardingStep,
            schoologyConnected: userRecord.schoologyConnected,
          }
        : null,
      courses,
      toolCalls,
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

export const getGenerationOwner = internalQuery({
  args: {
    generationId: v.string(),
  },
  handler: async (ctx, args) => {
    const generationId = ctx.db.normalizeId("chatGenerations", args.generationId);
    if (!generationId) {
      return null;
    }
    const generation = await ctx.db.get(generationId);
    if (!generation) {
      return null;
    }
    return { userId: generation.userId };
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
    toolTraceSummary: v.optional(v.string()),
    toolTraceStats: v.optional(
      v.object({
        toolCallsCount: v.number(),
        coursesTouched: v.number(),
        assignmentsTouched: v.number(),
        documentsTouched: v.number(),
      }),
    ),
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
      toolTraceSummary: args.toolTraceSummary ?? generation.toolTraceSummary,
      toolTraceStats: args.toolTraceStats ?? generation.toolTraceStats,
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
    toolTraceSummary: v.optional(v.string()),
    toolTraceStats: v.optional(
      v.object({
        toolCallsCount: v.number(),
        coursesTouched: v.number(),
        assignmentsTouched: v.number(),
        documentsTouched: v.number(),
      }),
    ),
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
      toolTraceSummary: args.toolTraceSummary ?? generation.toolTraceSummary,
      toolTraceStats: args.toolTraceStats ?? generation.toolTraceStats,
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
    toolTraceSummary: v.optional(v.string()),
    toolTraceStats: v.optional(
      v.object({
        toolCallsCount: v.number(),
        coursesTouched: v.number(),
        assignmentsTouched: v.number(),
        documentsTouched: v.number(),
      }),
    ),
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
      toolTraceSummary: args.toolTraceSummary ?? generation.toolTraceSummary,
      toolTraceStats: args.toolTraceStats ?? generation.toolTraceStats,
      errorCode: undefined,
      errorMessage: undefined,
      updatedAt: args.completedAt,
      completedAt: args.completedAt,
      lastTextAt: args.content ? args.completedAt : generation.lastTextAt,
    });

    return ctx.db.get(generation._id);
  },
});

export const upsertToolCall = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    sequence: v.number(),
    callId: v.string(),
    toolName: v.string(),
    status: v.union(
      v.literal("pending"),
      v.literal("running"),
      v.literal("completed"),
      v.literal("failed"),
    ),
    argumentsText: v.optional(v.string()),
    outputText: v.optional(v.string()),
    summaryText: v.optional(v.string()),
    errorText: v.optional(v.string()),
    startedAt: v.optional(v.number()),
    completedAt: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    const now = Date.now();
    const existing = await ctx.db
      .query("chatToolCalls")
      .withIndex("by_generation_call", (q) =>
        q.eq("generationId", args.generationId).eq("callId", args.callId),
      )
      .first();

    if (existing) {
      await ctx.db.patch(existing._id, {
        sequence: args.sequence,
        toolName: args.toolName,
        status: args.status,
        argumentsText: args.argumentsText ?? existing.argumentsText,
        outputText: args.outputText ?? existing.outputText,
        summaryText: args.summaryText ?? existing.summaryText,
        errorText: args.errorText ?? existing.errorText,
        startedAt: args.startedAt ?? existing.startedAt,
        completedAt: args.completedAt ?? existing.completedAt,
        updatedAt: now,
      });
      return ctx.db.get(existing._id);
    }

    const toolCallId = await ctx.db.insert("chatToolCalls", {
      generationId: args.generationId,
      threadId: generation.threadId,
      userId: generation.userId,
      sequence: args.sequence,
      callId: args.callId,
      toolName: args.toolName,
      status: args.status,
      argumentsText: args.argumentsText,
      outputText: args.outputText,
      summaryText: args.summaryText,
      errorText: args.errorText,
      startedAt: args.startedAt,
      completedAt: args.completedAt,
      createdAt: now,
      updatedAt: now,
    });

    return ctx.db.get(toolCallId);
  },
});

export const updateGenerationToolTraceSummary = internalMutation({
  args: {
    generationId: v.id("chatGenerations"),
    toolTraceSummary: v.string(),
    toolTraceStats: v.object({
      toolCallsCount: v.number(),
      coursesTouched: v.number(),
      assignmentsTouched: v.number(),
      documentsTouched: v.number(),
    }),
  },
  handler: async (ctx, args) => {
    const generation = await getGenerationById(ctx.db, args.generationId);
    if (!generation) {
      throw new Error("Generation not found");
    }

    await ctx.db.patch(generation._id, {
      toolTraceSummary: args.toolTraceSummary,
      toolTraceStats: args.toolTraceStats,
      updatedAt: Date.now(),
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

export const updateThreadTitle = internalMutation({
  args: {
    threadId: v.id("chatThreads"),
    title: v.string(),
  },
  handler: async (ctx, args) => {
    const thread = await ctx.db.get(args.threadId);
    if (!thread) return;
    const title = args.title.trim().slice(0, 32);
    if (!title) return;
    await ctx.db.patch(args.threadId, { title });
  },
});

export const kickoffBackendGeneration = internalAction({
  args: {
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args): Promise<{ skipped: boolean; reason?: string; status?: number }> => {
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
        body: JSON.stringify({ generationId: args.generationId, userId: context.userId }),
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
