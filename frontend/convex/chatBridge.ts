import { v } from "convex/values";

import { internal } from "./_generated/api";
import { action } from "./_generated/server";

function assertSecret(secret: string) {
  const expected = process.env.CHAT_INTERNAL_SECRET;
  if (!expected || secret !== expected) {
    throw new Error("invalid_bridge_secret");
  }
}

export const getGenerationContext = action({
  args: {
    secret: v.string(),
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runQuery(internal.chatInternal.getGenerationContext, {
      generationId: args.generationId,
    });
  },
});

export const getGenerationCancelState = action({
  args: {
    secret: v.string(),
    generationId: v.id("chatGenerations"),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runQuery(internal.chatInternal.getGenerationCancelState, {
      generationId: args.generationId,
    });
  },
});

export const markGenerationStreaming = action({
  args: {
    secret: v.string(),
    generationId: v.id("chatGenerations"),
    startedAt: v.number(),
    provider: v.optional(v.string()),
    model: v.optional(v.string()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.chatInternal.markGenerationStreaming, {
      generationId: args.generationId,
      startedAt: args.startedAt,
      provider: args.provider,
      model: args.model,
    });
  },
});

export const heartbeatGeneration = action({
  args: {
    secret: v.string(),
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
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.chatInternal.heartbeatGeneration, {
      generationId: args.generationId,
      updatedAt: args.updatedAt,
      lastTextAt: args.lastTextAt,
      activity: args.activity,
    });
  },
});

export const markGenerationCompleted = action({
  args: {
    secret: v.string(),
    generationId: v.id("chatGenerations"),
    content: v.string(),
    completedAt: v.number(),
    providerMessageId: v.optional(v.string()),
    usage: v.optional(v.any()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.chatInternal.markGenerationCompleted, {
      generationId: args.generationId,
      content: args.content,
      completedAt: args.completedAt,
      providerMessageId: args.providerMessageId,
      usage: args.usage,
    });
  },
});

export const markGenerationFailed = action({
  args: {
    secret: v.string(),
    generationId: v.id("chatGenerations"),
    errorCode: v.string(),
    errorMessage: v.string(),
    completedAt: v.number(),
    content: v.optional(v.string()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.chatInternal.markGenerationFailed, {
      generationId: args.generationId,
      errorCode: args.errorCode,
      errorMessage: args.errorMessage,
      completedAt: args.completedAt,
      content: args.content,
    });
  },
});

export const markGenerationCancelled = action({
  args: {
    secret: v.string(),
    generationId: v.id("chatGenerations"),
    completedAt: v.number(),
    content: v.optional(v.string()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.chatInternal.markGenerationCancelled, {
      generationId: args.generationId,
      completedAt: args.completedAt,
      content: args.content,
    });
  },
});
