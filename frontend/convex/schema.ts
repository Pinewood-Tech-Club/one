import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  userPreferences: defineTable({
    userId: v.string(),
    sidebarCollapsed: v.boolean(),
  }).index("by_user", ["userId"]),

  chatThreads: defineTable({
    userId: v.string(),
    title: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
    lastMessageAt: v.number(),
    archivedAt: v.optional(v.number()),
  })
    .index("by_user", ["userId"])
    .index("by_user_updated", ["userId", "updatedAt"]),

  chatMessages: defineTable({
    threadId: v.id("chatThreads"),
    userId: v.string(),
    role: v.union(
      v.literal("user"),
      v.literal("assistant"),
      v.literal("system")
    ),
    content: v.string(),
    status: v.union(
      v.literal("queued"),
      v.literal("streaming"),
      v.literal("completed"),
      v.literal("failed"),
      v.literal("cancelled")
    ),
    chunkSequence: v.optional(v.number()),
    providerMessageId: v.optional(v.string()),
    error: v.optional(v.string()),
    createdAt: v.number(),
    updatedAt: v.number(),
    completedAt: v.optional(v.number()),
  })
    .index("by_thread_created", ["threadId", "createdAt"])
    .index("by_user_thread", ["userId", "threadId"]),

  chatGenerations: defineTable({
    threadId: v.id("chatThreads"),
    userId: v.string(),
    userMessageId: v.id("chatMessages"),
    assistantMessageId: v.id("chatMessages"),
    clientRequestId: v.string(),
    status: v.union(
      v.literal("queued"),
      v.literal("streaming"),
      v.literal("completed"),
      v.literal("failed"),
      v.literal("cancelled")
    ),
    activity: v.optional(
      v.union(
        v.literal("thinking"),
        v.literal("streaming_text"),
        v.literal("tool_running"),
        v.literal("post_tool_reasoning")
      )
    ),
    provider: v.string(),
    model: v.string(),
    cancelRequested: v.boolean(),
    errorCode: v.optional(v.string()),
    errorMessage: v.optional(v.string()),
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
    createdAt: v.number(),
    startedAt: v.optional(v.number()),
    updatedAt: v.number(),
    completedAt: v.optional(v.number()),
    lastTextAt: v.optional(v.number()),
  })
    .index("by_thread_status", ["threadId", "status"])
    .index("by_user", ["userId"])
    .index("by_status_updated", ["status", "updatedAt"])
    .index("by_assistant_message", ["assistantMessageId"])
    .index("by_user_request", ["userId", "clientRequestId"]),

  chatToolCalls: defineTable({
    generationId: v.id("chatGenerations"),
    threadId: v.id("chatThreads"),
    userId: v.string(),
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
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_generation_sequence", ["generationId", "sequence"])
    .index("by_generation_call", ["generationId", "callId"])
    .index("by_thread_created", ["threadId", "createdAt"]),

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
