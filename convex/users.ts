import { v } from "convex/values";
import { query, mutation } from "./_generated/server";
import { getOptionalAuthenticatedUser } from "./auth";

// ============================================================================
// QUERIES - Frontend reads user data (auth-protected)
// ============================================================================

/**
 * Get current user data including onboarding state
 * Used by frontend to determine which onboarding step to show
 */
export const getUser = query({
  args: {},
  handler: async (ctx) => {
    const identity = await getOptionalAuthenticatedUser(ctx);
    if (!identity) {
      return null;
    }

    const user = await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", identity.userId))
      .first();

    return user;
  },
});

/**
 * Get user by userId (for backend queries)
 * Used when backend needs to check user state
 */
export const getUserByUserId = query({
  args: {
    userId: v.string(),
  },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();
  },
});

// ============================================================================
// MUTATIONS - Backend updates user data
// These are called by the trusted backend after user authentication
// ============================================================================

/**
 * Get or create user record
 * Called by backend after Google OAuth login
 */
export const getOrCreate = mutation({
  args: {
    userId: v.string(),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();

    if (existing) {
      return existing;
    }

    const now = Date.now();
    const id = await ctx.db.insert("users", {
      userId: args.userId,
      onboardingStep: "welcome",
      schoologyConnected: false,
      createdAt: now,
      updatedAt: now,
    });

    return await ctx.db.get(id);
  },
});

/**
 * Update onboarding step
 * Called by backend at various onboarding transitions
 */
export const updateOnboardingStep = mutation({
  args: {
    userId: v.string(),
    step: v.union(
      v.literal("welcome"),
      v.literal("connect_lms"),
      v.literal("smart_consent"),
      v.literal("completed")
    ),
  },
  handler: async (ctx, args) => {
    const user = await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();

    if (!user) {
      throw new Error("User not found");
    }

    await ctx.db.patch(user._id, {
      onboardingStep: args.step,
      updatedAt: Date.now(),
    });

    return { success: true };
  },
});

/**
 * Update Schoology connection status
 * Called by backend after successful Schoology OAuth
 */
export const updateSchoologyConnected = mutation({
  args: {
    userId: v.string(),
    connected: v.boolean(),
  },
  handler: async (ctx, args) => {
    const user = await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();

    if (!user) {
      throw new Error("User not found");
    }

    await ctx.db.patch(user._id, {
      schoologyConnected: args.connected,
      updatedAt: Date.now(),
    });

    return { success: true };
  },
});

/**
 * Update user's Schoology profile picture URL
 * Called by backend after fetching user info from Schoology
 */
export const updateProfilePicture = mutation({
  args: {
    userId: v.string(),
    pictureUrl: v.string(),
  },
  handler: async (ctx, args) => {
    const user = await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();

    if (!user) {
      throw new Error("User not found");
    }

    await ctx.db.patch(user._id, {
      profilePictureUrl: args.pictureUrl,
      updatedAt: Date.now(),
    });

    return { success: true };
  },
});

/**
 * Save smart features consent and complete onboarding
 * Called by backend when user submits consent form
 */
export const saveConsent = mutation({
  args: {
    userId: v.string(),
    consent: v.object({
      enabled: v.boolean(),
      timestamp: v.number(),
      version: v.string(),
    }),
  },
  handler: async (ctx, args) => {
    const user = await ctx.db
      .query("users")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();

    if (!user) {
      throw new Error("User not found");
    }

    await ctx.db.patch(user._id, {
      smartFeaturesConsent: args.consent,
      onboardingStep: "completed",
      updatedAt: Date.now(),
    });

    return { success: true };
  },
});
