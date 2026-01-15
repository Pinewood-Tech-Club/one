/**
 * Authentication helpers for Convex functions
 */
import { QueryCtx, MutationCtx } from "./_generated/server";

export interface AuthenticatedUser {
  userId: string;
  email: string | undefined;
  name: string | undefined;
}

/**
 * Get the authenticated user from the JWT identity.
 * Throws an error if the user is not authenticated.
 */
export async function getAuthenticatedUser(
  ctx: QueryCtx | MutationCtx
): Promise<AuthenticatedUser> {
  const identity = await ctx.auth.getUserIdentity();
  if (!identity) {
    throw new Error("Not authenticated");
  }
  return {
    userId: identity.subject, // "sub" claim from JWT
    email: identity.email,
    name: identity.name,
  };
}

/**
 * Get the authenticated user, or null if not authenticated.
 * Use this for queries that should return empty results for unauthenticated users.
 */
export async function getOptionalAuthenticatedUser(
  ctx: QueryCtx | MutationCtx
): Promise<AuthenticatedUser | null> {
  const identity = await ctx.auth.getUserIdentity();
  if (!identity) {
    return null;
  }
  return {
    userId: identity.subject,
    email: identity.email,
    name: identity.name,
  };
}
