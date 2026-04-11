import { v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { getAuthenticatedUser, getOptionalAuthenticatedUser } from "./auth";

// Get user's sidebar preference (auth-protected)
export const getSidebarCollapsed = query({
  args: {},
  handler: async (ctx) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return false;
    }

    const preference = await ctx.db
      .query("userPreferences")
      .withIndex("by_user", (q) => q.eq("userId", user.userId))
      .first();

    return preference?.sidebarCollapsed ?? false;
  },
});

// Set user's sidebar preference (auth-protected)
export const setSidebarCollapsed = mutation({
  args: {
    collapsed: v.boolean(),
  },
  handler: async (ctx, args) => {
    const user = await getAuthenticatedUser(ctx);

    const existing = await ctx.db
      .query("userPreferences")
      .withIndex("by_user", (q) => q.eq("userId", user.userId))
      .first();

    if (existing) {
      await ctx.db.patch(existing._id, {
        sidebarCollapsed: args.collapsed,
      });
    } else {
      await ctx.db.insert("userPreferences", {
        userId: user.userId,
        sidebarCollapsed: args.collapsed,
      });
    }
  },
});
