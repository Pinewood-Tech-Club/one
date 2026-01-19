import { v } from "convex/values";
import { query, mutation } from "./_generated/server";
import { getOptionalAuthenticatedUser } from "./auth";

// ============================================================================
// QUERIES - Frontend reads cached data (auth-protected)
// ============================================================================

/**
 * Get all cached courses for the authenticated user
 * Returns full course objects from Schoology API
 */
export const getCourses = query({
  args: {},
  handler: async (ctx) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return [];
    }

    const courses = await ctx.db
      .query("schoologyCourses")
      .withIndex("by_user", (q) => q.eq("userId", user.userId))
      .collect();

    return courses.map(course => ({
      ...course.data, // Spread the full Schoology object
      _lastUpdated: course.lastUpdated, // Add metadata
    }));
  },
});

/**
 * Get all cached assignments for the authenticated user
 * Returns full assignment objects from Schoology API
 */
export const getAssignments = query({
  args: {},
  handler: async (ctx) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return [];
    }

    const assignments = await ctx.db
      .query("schoologyAssignments")
      .withIndex("by_user", (q) => q.eq("userId", user.userId))
      .collect();

    return assignments.map(assignment => ({
      ...assignment.data, // Spread the full Schoology object
      _courseId: assignment.courseId, // Add metadata
      _lastUpdated: assignment.lastUpdated,
    }));
  },
});

/**
 * Get assignments for a specific course
 * Returns full assignment objects from Schoology API
 */
export const getAssignmentsByCourse = query({
  args: {
    courseId: v.string(),
  },
  handler: async (ctx, args) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return [];
    }

    const assignments = await ctx.db
      .query("schoologyAssignments")
      .withIndex("by_user_and_course", (q) =>
        q.eq("userId", user.userId).eq("courseId", args.courseId)
      )
      .collect();

    return assignments.map(assignment => ({
      ...assignment.data, // Spread the full Schoology object
      _courseId: assignment.courseId, // Add metadata
      _lastUpdated: assignment.lastUpdated,
    }));
  },
});

// ============================================================================
// MUTATIONS - Backend updates cached data
// These are called by the trusted backend after user authentication
// ============================================================================

/**
 * Update courses cache for a user
 * Called by backend after fetching from Schoology API
 * Stores full course objects for maximum flexibility
 */
export const updateCourses = mutation({
  args: {
    userId: v.string(),
    courses: v.array(v.any()), // Accept full course objects
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();

    // Get existing courses for this user
    const existingCourses = await ctx.db
      .query("schoologyCourses")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    // Create a map of existing courses by courseId
    const existingMap = new Map(
      existingCourses.map(c => [c.courseId, c])
    );

    // Track which courses we've seen in the new data
    const seenCourseIds = new Set<string>();

    // Update or insert courses
    for (const course of args.courses) {
      const courseId = String(course.id);
      seenCourseIds.add(courseId);
      const existing = existingMap.get(courseId);

      if (existing) {
        // Update existing course with full data
        await ctx.db.patch(existing._id, {
          data: course,
          lastUpdated: timestamp,
        });
      } else {
        // Insert new course with full data
        await ctx.db.insert("schoologyCourses", {
          userId: args.userId,
          courseId: courseId,
          data: course,
          lastUpdated: timestamp,
        });
      }
    }

    // Delete courses that are no longer in Schoology
    for (const existing of existingCourses) {
      if (!seenCourseIds.has(existing.courseId)) {
        await ctx.db.delete(existing._id);
      }
    }

    return { success: true, count: args.courses.length };
  },
});

/**
 * Update assignments cache for a user
 * Called by backend after fetching from Schoology API
 * Stores full assignment objects for maximum flexibility
 */
export const updateAssignments = mutation({
  args: {
    userId: v.string(),
    courseId: v.string(),
    assignments: v.array(v.any()), // Accept full assignment objects
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();

    // Get existing assignments for this course
    const existingAssignments = await ctx.db
      .query("schoologyAssignments")
      .withIndex("by_user_and_course", (q) =>
        q.eq("userId", args.userId).eq("courseId", args.courseId)
      )
      .collect();

    // Create a map of existing assignments by assignmentId
    const existingMap = new Map(
      existingAssignments.map(a => [a.assignmentId, a])
    );

    // Track which assignments we've seen in the new data
    const seenAssignmentIds = new Set<string>();

    // Update or insert assignments
    for (const assignment of args.assignments) {
      const assignmentId = String(assignment.id);
      seenAssignmentIds.add(assignmentId);
      const existing = existingMap.get(assignmentId);

      if (existing) {
        // Update existing assignment with full data
        await ctx.db.patch(existing._id, {
          data: assignment,
          lastUpdated: timestamp,
        });
      } else {
        // Insert new assignment with full data
        await ctx.db.insert("schoologyAssignments", {
          userId: args.userId,
          courseId: args.courseId,
          assignmentId: assignmentId,
          data: assignment,
          lastUpdated: timestamp,
        });
      }
    }

    // Delete assignments that are no longer in Schoology
    for (const existing of existingAssignments) {
      if (!seenAssignmentIds.has(existing.assignmentId)) {
        await ctx.db.delete(existing._id);
      }
    }

    return { success: true, count: args.assignments.length };
  },
});

/**
 * Update upcoming assignments cache for a user
 * Called by backend after fetching from Schoology API
 */
export const updateUpcoming = mutation({
  args: {
    userId: v.string(),
    assignments: v.array(v.any()), // Accept full assignment objects
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();

    // Delete existing upcoming assignments for this user
    // We replace the entire list because it's a "snapshot" of upcoming items
    const existing = await ctx.db
      .query("schoologyUpcoming")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    for (const item of existing) {
      await ctx.db.delete(item._id);
    }

    // Insert new assignments
    for (const assignment of args.assignments) {
      await ctx.db.insert("schoologyUpcoming", {
        userId: args.userId,
        assignmentId: String(assignment.id),
        data: assignment,
        courseTitle: assignment.course_title || "Unknown Course",
        dueDate: assignment.due,
        lastUpdated: timestamp,
      });
    }

    return { success: true, count: args.assignments.length };
  },
});

/**
 * Clear all cached data for a user
 * Useful when user disconnects Schoology
 */
export const clearCache = mutation({
  args: { userId: v.string() },
  handler: async (ctx, args) => {
    // Delete all courses
    const courses = await ctx.db
      .query("schoologyCourses")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    for (const course of courses) {
      await ctx.db.delete(course._id);
    }

    // Delete all assignments
    const assignments = await ctx.db
      .query("schoologyAssignments")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    for (const assignment of assignments) {
      await ctx.db.delete(assignment._id);
    }

    return { success: true };
  },
});
