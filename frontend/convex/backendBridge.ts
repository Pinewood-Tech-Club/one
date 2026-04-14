import { v } from "convex/values";

import { internal } from "./_generated/api";
import { action } from "./_generated/server";

function assertSecret(secret: string) {
  const expected =
    process.env.CONVEX_BRIDGE_SECRET ?? process.env.CHAT_INTERNAL_SECRET;
  if (!expected || secret !== expected) {
    throw new Error("invalid_bridge_secret");
  }
}

const onboardingStep = v.union(
  v.literal("welcome"),
  v.literal("connect_lms"),
  v.literal("smart_consent"),
  v.literal("completed"),
);

export const getUserByUserId = action({
  args: {
    secret: v.string(),
    userId: v.string(),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runQuery(internal.users.getUserByUserId, {
      userId: args.userId,
    });
  },
});

export const getOrCreateUser = action({
  args: {
    secret: v.string(),
    userId: v.string(),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.users.getOrCreate, {
      userId: args.userId,
    });
  },
});

export const updateOnboardingStep = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    step: onboardingStep,
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.users.updateOnboardingStep, {
      userId: args.userId,
      step: args.step,
    });
  },
});

export const updateSchoologyConnected = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    connected: v.boolean(),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.users.updateSchoologyConnected, {
      userId: args.userId,
      connected: args.connected,
    });
  },
});

export const updateProfilePicture = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    pictureUrl: v.string(),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.users.updateProfilePicture, {
      userId: args.userId,
      pictureUrl: args.pictureUrl,
    });
  },
});

export const saveConsent = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    consent: v.object({
      enabled: v.boolean(),
      timestamp: v.number(),
      version: v.string(),
    }),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.users.saveConsent, {
      userId: args.userId,
      consent: args.consent,
    });
  },
});

export const updateCourses = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    courses: v.array(v.any()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.schoologyCache.updateCourses, {
      userId: args.userId,
      courses: args.courses,
    });
  },
});

export const updateAssignments = action({
  args: {
    secret: v.string(),
    courseId: v.string(),
    assignments: v.array(v.any()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.schoologyCache.updateAssignments, {
      courseId: args.courseId,
      assignments: args.assignments,
    });
  },
});

export const updateAssignmentUserState = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    courseId: v.string(),
    assignments: v.array(v.any()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.schoologyCache.updateAssignmentUserState, {
      userId: args.userId,
      courseId: args.courseId,
      assignments: args.assignments,
    });
  },
});

export const updateCourseAssignments = action({
  args: {
    secret: v.string(),
    userId: v.string(),
    courseId: v.string(),
    assignments: v.array(v.any()),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.schoologyCache.updateCourseAssignments, {
      userId: args.userId,
      courseId: args.courseId,
      assignments: args.assignments,
    });
  },
});

export const clearSchoologyCache = action({
  args: {
    secret: v.string(),
    userId: v.string(),
  },
  handler: async (ctx, args): Promise<any> => {
    assertSecret(args.secret);
    return await ctx.runMutation(internal.schoologyCache.clearCache, {
      userId: args.userId,
    });
  },
});
