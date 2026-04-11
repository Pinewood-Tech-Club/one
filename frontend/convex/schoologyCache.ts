import { v } from "convex/values";
import { query, mutation } from "./_generated/server";
import { getOptionalAuthenticatedUser } from "./auth";

const USER_STATE_KEYS = [
  "completed",
  "completion_status",
  "completion_code",
  "grade",
  "grade_comment",
  "collected_only",
  "dropbox_locked",
] as const;

function assignmentStateKey(courseId: string, assignmentId: string) {
  return `${courseId}:${assignmentId}`;
}

function parseDueToMs(dueRaw: unknown): number | undefined {
  if (dueRaw === null || dueRaw === undefined) {
    return undefined;
  }

  if (typeof dueRaw === "number" && Number.isFinite(dueRaw)) {
    return dueRaw > 1e11 ? Math.trunc(dueRaw) : Math.trunc(dueRaw * 1000);
  }

  const dueStr = String(dueRaw).trim();
  if (!dueStr) {
    return undefined;
  }

  const numericDue = Number(dueStr);
  if (!Number.isNaN(numericDue)) {
    return numericDue > 1e11 ? Math.trunc(numericDue) : Math.trunc(numericDue * 1000);
  }

  const isoCandidate = dueStr.includes("T")
    ? dueStr
    : dueStr.includes(" ")
      ? dueStr.replace(" ", "T")
      : dueStr;
  const normalized = isoCandidate.endsWith("Z") ? isoCandidate : isoCandidate;
  const parsed = Date.parse(normalized);
  if (!Number.isNaN(parsed)) {
    return parsed;
  }

  return undefined;
}

function extractAssignmentUserState(assignment: any) {
  const stateData: Record<string, any> = {};
  for (const key of USER_STATE_KEYS) {
    if (assignment[key] !== undefined) {
      stateData[key] = assignment[key];
    }
  }

  const completedRaw = assignment.completed;
  let completed: boolean | undefined;
  if (completedRaw !== undefined && completedRaw !== null) {
    if (typeof completedRaw === "boolean") {
      completed = completedRaw;
    } else if (typeof completedRaw === "number") {
      completed = completedRaw !== 0;
    } else if (typeof completedRaw === "string") {
      const normalized = completedRaw.trim().toLowerCase();
      if (normalized === "1" || normalized === "true") {
        completed = true;
      } else if (normalized === "0" || normalized === "false") {
        completed = false;
      }
    }
  }

  return {
    completed,
    completionStatus:
      assignment.completion_status !== undefined
        ? String(assignment.completion_status)
        : undefined,
    grade: assignment.grade !== undefined ? String(assignment.grade) : undefined,
    data: Object.keys(stateData).length > 0 ? stateData : undefined,
  };
}

async function getActiveCourseMemberships(ctx: any, userId: string) {
  const memberships = await ctx.db
    .query("schoologyCourseMemberships")
    .withIndex("by_user", (q: any) => q.eq("userId", userId))
    .collect();

  return memberships.filter((membership: any) => membership.isActive);
}

async function getCourseMap(ctx: any, courseIds: string[]) {
  const map = new Map<string, any>();
  for (const courseId of courseIds) {
    const rows = await ctx.db
      .query("schoologyCourses")
      .withIndex("by_course", (q: any) => q.eq("courseId", courseId))
      .collect();

    if (!rows.length) {
      continue;
    }

    rows.sort(
      (a: any, b: any) =>
        (b.lastSyncedAt ?? b.lastUpdated ?? 0) - (a.lastSyncedAt ?? a.lastUpdated ?? 0)
    );
    map.set(courseId, rows[0]);
  }
  return map;
}

async function getUserStateMap(ctx: any, userId: string) {
  const states = await ctx.db
    .query("schoologyAssignmentUserState")
    .withIndex("by_user", (q: any) => q.eq("userId", userId))
    .collect();

  return new Map(
    states.map((state: any) => [assignmentStateKey(state.courseId, state.assignmentId), state])
  );
}

function mergeAssignmentRecord(
  assignmentRow: any,
  courseRow: any,
  userState: any,
) {
  const assignmentLastSyncedAt =
    assignmentRow.lastSyncedAt ??
    assignmentRow.lastUpdated ??
    Date.now();

  const merged = {
    ...assignmentRow.data,
    ...(userState?.data || {}),
    section_id: assignmentRow.courseId,
    course_title:
      assignmentRow.data.course_title ||
      courseRow?.data?.course_title ||
      courseRow?.data?.title ||
      "",
    section_title:
      assignmentRow.data.section_title ||
      courseRow?.data?.section_title ||
      "",
    _courseId: assignmentRow.courseId,
    _lastUpdated: assignmentLastSyncedAt,
  };

  if (userState?.completed !== undefined) {
    merged.completed = userState.completed;
  }
  if (userState?.completionStatus !== undefined) {
    merged.completion_status = userState.completionStatus;
  }
  if (userState?.grade !== undefined) {
    merged.grade = userState.grade;
  }

  return merged;
}

function getAssignmentId(assignment: any) {
  return String(assignment.id || assignment.grade_item_id || "");
}

async function upsertSharedAssignmentsForCourse(
  ctx: any,
  courseId: string,
  assignments: any[],
  timestamp: number,
) {
  const existingAssignments = await ctx.db
    .query("schoologyAssignments")
    .withIndex("by_course", (q: any) => q.eq("courseId", courseId))
    .collect();

  const existingMap = new Map<string, any>(
    existingAssignments.map((assignment: any) => [assignment.assignmentId, assignment])
  );

  const seenAssignmentIds = new Set<string>();

  for (const assignment of assignments) {
    const assignmentId = getAssignmentId(assignment);
    if (!assignmentId) {
      continue;
    }

    seenAssignmentIds.add(assignmentId);

    const dueRaw =
      assignment.due !== undefined && assignment.due !== null
        ? String(assignment.due)
        : undefined;
    const dueAtMs = parseDueToMs(assignment.due);

    const existing = existingMap.get(assignmentId);
    if (existing) {
      await ctx.db.patch(existing._id, {
        data: assignment,
        dueRaw,
        dueAtMs,
        lastSyncedAt: timestamp,
      });
    } else {
      await ctx.db.insert("schoologyAssignments", {
        courseId,
        assignmentId,
        data: assignment,
        dueRaw,
        dueAtMs,
        lastSyncedAt: timestamp,
      });
    }
  }

  for (const existing of existingAssignments) {
    if (!seenAssignmentIds.has(existing.assignmentId)) {
      await ctx.db.delete(existing._id);
    }
  }

  return seenAssignmentIds.size;
}

async function upsertAssignmentUserStateForCourse(
  ctx: any,
  userId: string,
  courseId: string,
  assignments: any[],
  timestamp: number,
) {
  const existingStates = await ctx.db
    .query("schoologyAssignmentUserState")
    .withIndex("by_user_and_course", (q: any) =>
      q.eq("userId", userId).eq("courseId", courseId)
    )
    .collect();

  const existingMap = new Map<string, any>(
    existingStates.map((state: any) => [state.assignmentId, state])
  );

  const seenAssignmentIds = new Set<string>();

  for (const assignment of assignments) {
    const assignmentId = getAssignmentId(assignment);
    if (!assignmentId) {
      continue;
    }

    seenAssignmentIds.add(assignmentId);
    const userState = extractAssignmentUserState(assignment);
    const existing = existingMap.get(assignmentId);

    if (existing) {
      await ctx.db.patch(existing._id, {
        completed: userState.completed,
        completionStatus: userState.completionStatus,
        grade: userState.grade,
        data: userState.data,
        lastSyncedAt: timestamp,
      });
    } else {
      await ctx.db.insert("schoologyAssignmentUserState", {
        userId,
        courseId,
        assignmentId,
        completed: userState.completed,
        completionStatus: userState.completionStatus,
        grade: userState.grade,
        data: userState.data,
        lastSyncedAt: timestamp,
      });
    }
  }

  for (const existing of existingStates) {
    if (!seenAssignmentIds.has(existing.assignmentId)) {
      await ctx.db.delete(existing._id);
    }
  }

  return seenAssignmentIds.size;
}

// ============================================================================
// QUERIES - Frontend reads cached data (auth-protected)
// ============================================================================

/**
 * Get all cached courses for the authenticated user.
 */
export const getCourses = query({
  args: {},
  handler: async (ctx) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return [];
    }

    const memberships = await getActiveCourseMemberships(ctx, user.userId);
    const courseIds: string[] = Array.from(
      new Set<string>(
        memberships.map((membership: any): string => String(membership.courseId))
      )
    );
    const courseMap = await getCourseMap(ctx, courseIds);

    return courseIds
      .map((courseId) => {
        const course = courseMap.get(courseId);
        if (!course) {
          return null;
        }
        return {
          ...course.data,
          _lastUpdated: course.lastSyncedAt ?? course.lastUpdated ?? Date.now(),
        };
      })
      .filter(Boolean);
  },
});

/**
 * Get all cached assignments for the authenticated user.
 */
export const getAssignments = query({
  args: {},
  handler: async (ctx) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return [];
    }

    const memberships = await getActiveCourseMemberships(ctx, user.userId);
    const courseIds: string[] = Array.from(
      new Set<string>(
        memberships.map((membership: any): string => String(membership.courseId))
      )
    );
    const courseMap = await getCourseMap(ctx, courseIds);
    const userStateMap = await getUserStateMap(ctx, user.userId);

    const results: any[] = [];
    for (const courseId of courseIds) {
      const assignments = await ctx.db
        .query("schoologyAssignments")
        .withIndex("by_course", (q: any) => q.eq("courseId", courseId))
        .collect();

      for (const assignment of assignments) {
        const state = userStateMap.get(
          assignmentStateKey(courseId, String(assignment.assignmentId))
        );
        results.push(mergeAssignmentRecord(assignment, courseMap.get(courseId), state));
      }
    }

    return results;
  },
});

/**
 * Get assignments for a specific course.
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

    const membership = await ctx.db
      .query("schoologyCourseMemberships")
      .withIndex("by_user_and_course", (q) =>
        q.eq("userId", user.userId).eq("courseId", args.courseId)
      )
      .unique();

    if (!membership || !membership.isActive) {
      return [];
    }

    const assignments = await ctx.db
      .query("schoologyAssignments")
      .withIndex("by_course", (q) => q.eq("courseId", args.courseId))
      .collect();

    const states = await ctx.db
      .query("schoologyAssignmentUserState")
      .withIndex("by_user_and_course", (q) =>
        q.eq("userId", user.userId).eq("courseId", args.courseId)
      )
      .collect();
    const stateMap = new Map(
      states.map((state) => [assignmentStateKey(args.courseId, String(state.assignmentId)), state])
    );

    const courseRows = await ctx.db
      .query("schoologyCourses")
      .withIndex("by_course", (q) => q.eq("courseId", args.courseId))
      .collect();
    const course = courseRows.length ? courseRows[0] : null;

    return assignments.map((assignment) =>
      mergeAssignmentRecord(
        assignment,
        course,
        stateMap.get(assignmentStateKey(args.courseId, String(assignment.assignmentId)))
      )
    );
  },
});

/**
 * Get upcoming assignments for the authenticated user.
 * Returns all future assignments (due >= now), sorted by due date ascending.
 */
export const getUpcoming = query({
  args: {},
  handler: async (ctx) => {
    const user = await getOptionalAuthenticatedUser(ctx);
    if (!user) {
      return [];
    }

    const memberships = await getActiveCourseMemberships(ctx, user.userId);
    const courseIds: string[] = Array.from(
      new Set<string>(
        memberships.map((membership: any): string => String(membership.courseId))
      )
    );
    const courseMap = await getCourseMap(ctx, courseIds);
    const userStateMap = await getUserStateMap(ctx, user.userId);
    const nowMs = Date.now();

    const upcomingRows: any[] = [];

    for (const courseId of courseIds) {
      const assignments = await ctx.db
        .query("schoologyAssignments")
        .withIndex("by_course_and_due", (q) =>
          q.eq("courseId", courseId).gte("dueAtMs", nowMs)
        )
        .collect();

      for (const assignment of assignments) {
        upcomingRows.push(assignment);
      }
    }

    upcomingRows.sort((a, b) => {
      const dueA = a.dueAtMs ?? parseDueToMs(a.dueRaw ?? a.data?.due) ?? Number.MAX_SAFE_INTEGER;
      const dueB = b.dueAtMs ?? parseDueToMs(b.dueRaw ?? b.data?.due) ?? Number.MAX_SAFE_INTEGER;
      return dueA - dueB;
    });

    return upcomingRows.map((assignment) => {
      const courseId = String(assignment.courseId);
      const state = userStateMap.get(
        assignmentStateKey(courseId, String(assignment.assignmentId))
      );
      return mergeAssignmentRecord(assignment, courseMap.get(courseId), state);
    });
  },
});

// ============================================================================
// MUTATIONS - Backend updates cached data
// These are called by the trusted backend after user authentication
// ============================================================================

/**
 * Update courses cache and memberships for a user.
 */
export const updateCourses = mutation({
  args: {
    userId: v.string(),
    courses: v.array(v.any()),
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();

    const existingMemberships = await ctx.db
      .query("schoologyCourseMemberships")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    const membershipMap = new Map(
      existingMemberships.map((membership) => [membership.courseId, membership])
    );

    const seenCourseIds = new Set<string>();

    for (const course of args.courses) {
      const courseId = String(course.id || "");
      if (!courseId) {
        continue;
      }

      seenCourseIds.add(courseId);

      const existingCourses = await ctx.db
        .query("schoologyCourses")
        .withIndex("by_course", (q) => q.eq("courseId", courseId))
        .collect();

      if (existingCourses.length > 0) {
        await ctx.db.patch(existingCourses[0]._id, {
          data: course,
          lastSyncedAt: timestamp,
        });

        // De-duplicate if stale duplicates exist.
        for (const duplicate of existingCourses.slice(1)) {
          await ctx.db.delete(duplicate._id);
        }
      } else {
        await ctx.db.insert("schoologyCourses", {
          courseId,
          data: course,
          lastSyncedAt: timestamp,
        });
      }

      const membership = membershipMap.get(courseId);
      const role =
        course.role !== undefined ? String(course.role) : undefined;
      if (membership) {
        await ctx.db.patch(membership._id, {
          role,
          isActive: true,
          lastSyncedAt: timestamp,
        });
      } else {
        await ctx.db.insert("schoologyCourseMemberships", {
          userId: args.userId,
          courseId,
          role,
          isActive: true,
          lastSyncedAt: timestamp,
        });
      }
    }

    for (const membership of existingMemberships) {
      if (!seenCourseIds.has(membership.courseId)) {
        await ctx.db.delete(membership._id);
      }
    }

    return { success: true, count: seenCourseIds.size };
  },
});

/**
 * Update shared assignments cache for a course.
 */
export const updateAssignments = mutation({
  args: {
    courseId: v.string(),
    assignments: v.array(v.any()),
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();
    const count = await upsertSharedAssignmentsForCourse(
      ctx,
      args.courseId,
      args.assignments,
      timestamp
    );
    return { success: true, count };
  },
});

/**
 * Update per-user assignment state for a course.
 */
export const updateAssignmentUserState = mutation({
  args: {
    userId: v.string(),
    courseId: v.string(),
    assignments: v.array(v.any()),
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();
    const count = await upsertAssignmentUserStateForCourse(
      ctx,
      args.userId,
      args.courseId,
      args.assignments,
      timestamp
    );
    return { success: true, count };
  },
});

/**
 * Update shared assignments cache and per-user assignment state for a course.
 */
export const updateCourseAssignments = mutation({
  args: {
    userId: v.string(),
    courseId: v.string(),
    assignments: v.array(v.any()),
  },
  handler: async (ctx, args) => {
    const timestamp = Date.now();
    const count = await upsertSharedAssignmentsForCourse(
      ctx,
      args.courseId,
      args.assignments,
      timestamp
    );
    await upsertAssignmentUserStateForCourse(
      ctx,
      args.userId,
      args.courseId,
      args.assignments,
      timestamp
    );
    return { success: true, count };
  },
});

/**
 * Clear cached user links/state while preserving shared course/assignment records.
 */
export const clearCache = mutation({
  args: { userId: v.string() },
  handler: async (ctx, args) => {
    const memberships = await ctx.db
      .query("schoologyCourseMemberships")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    for (const membership of memberships) {
      await ctx.db.delete(membership._id);
    }

    const states = await ctx.db
      .query("schoologyAssignmentUserState")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .collect();

    for (const state of states) {
      await ctx.db.delete(state._id);
    }

    return { success: true };
  },
});
