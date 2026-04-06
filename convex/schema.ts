import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  userPreferences: defineTable({
    userId: v.string(),
    sidebarCollapsed: v.boolean(),
  }).index("by_user", ["userId"]),

  // Schoology cache tables - normalized to avoid per-user duplication.
  schoologyCourses: defineTable({
    courseId: v.string(), // Schoology section ID
    data: v.any(), // Full section object from Schoology API
    lastSyncedAt: v.optional(v.number()), // timestamp (optional for legacy rows)
  })
    .index("by_course", ["courseId"]),

  schoologyCourseMemberships: defineTable({
    userId: v.string(),
    courseId: v.string(),
    role: v.optional(v.string()),
    isActive: v.boolean(),
    lastSyncedAt: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_course", ["courseId"])
    .index("by_user_and_course", ["userId", "courseId"]),

  schoologyAssignments: defineTable({
    courseId: v.string(), // Which course this assignment belongs to
    assignmentId: v.string(), // Schoology assignment ID
    dueAtMs: v.optional(v.number()), // Due date normalized to UTC milliseconds
    dueRaw: v.optional(v.string()), // Original due date string from Schoology
    data: v.any(), // Full assignment object from Schoology API
    lastSyncedAt: v.optional(v.number()), // timestamp (optional for legacy rows)
  })
    .index("by_course", ["courseId"])
    .index("by_course_and_assignment", ["courseId", "assignmentId"])
    .index("by_course_and_due", ["courseId", "dueAtMs"]),

  schoologyAssignmentUserState: defineTable({
    userId: v.string(),
    courseId: v.string(),
    assignmentId: v.string(), // Schoology assignment ID
    completed: v.optional(v.boolean()),
    completionStatus: v.optional(v.string()),
    grade: v.optional(v.string()),
    data: v.optional(v.any()),
    lastSyncedAt: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_user_and_course", ["userId", "courseId"])
    .index("by_user_and_assignment", ["userId", "assignmentId"]),

  // User onboarding state - stored in Convex for reactive frontend updates
  users: defineTable({
    userId: v.string(), // Backend user.id as string
    onboardingStep: v.union(
      v.literal("welcome"),
      v.literal("connect_lms"),
      v.literal("smart_consent"),
      v.literal("completed")
    ),
    smartFeaturesConsent: v.optional(
      v.object({
        enabled: v.boolean(),
        timestamp: v.number(),
        version: v.string(),
      })
    ),
    schoologyConnected: v.boolean(),
    profilePictureUrl: v.optional(v.string()),
    createdAt: v.number(),
    updatedAt: v.number(),
  }).index("by_user", ["userId"]),
});
