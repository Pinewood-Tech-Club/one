/**
 * Convex authentication configuration
 * Uses custom JWT provider with our Flask backend
 */

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:3111";

export default {
  providers: [
    {
      type: "customJwt" as const,
      applicationID: "convex",
      issuer: backendUrl,
      jwks: `${backendUrl}/api/.well-known/jwks.json`,
      algorithm: "RS256" as const,
    },
  ],
};
