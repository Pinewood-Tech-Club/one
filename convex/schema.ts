import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  userPreferences: defineTable({
    userId: v.string(),
    sidebarCollapsed: v.boolean(),
  }).index("by_user", ["userId"]),

  // Schoology cache tables - store full JSON objects for flexibility
  schoologyCourses: defineTable({
    userId: v.string(),
    courseId: v.string(), // Schoology section ID
    data: v.any(), // Full section object from Schoology API
    lastUpdated: v.number(), // timestamp
  })
    .index("by_user", ["userId"])
    .index("by_user_and_course", ["userId", "courseId"]),

  schoologyAssignments: defineTable({
    userId: v.string(),
    courseId: v.string(), // Which course this assignment belongs to
    assignmentId: v.string(), // Schoology assignment ID
    data: v.any(), // Full assignment object from Schoology API
    lastUpdated: v.number(), // timestamp
  })
    .index("by_user", ["userId"])
    .index("by_user_and_course", ["userId", "courseId"])
    .index("by_user_and_assignment", ["userId", "assignmentId"]),

  schoologyUpcoming: defineTable({
    userId: v.string(),
    assignmentId: v.string(), // Schoology assignment ID
    data: v.any(), // Full assignment object with course info
    courseTitle: v.string(), // Course title for filtering
    dueDate: v.string(), // Due date string for sorting
    lastUpdated: v.number(), // timestamp
  })
    .index("by_user", ["userId"])
    .index("by_user_and_due", ["userId", "dueDate"]),

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

