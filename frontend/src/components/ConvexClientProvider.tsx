'use client';

import { ConvexProvider, ConvexReactClient } from 'convex/react';
import { ReactNode, useEffect } from 'react';

const convex = new ConvexReactClient(process.env.NEXT_PUBLIC_CONVEX_URL!);

async function fetchConvexToken(): Promise<string | null> {
  try {
    const response = await fetch(
      `${process.env.NEXT_PUBLIC_BACKEND_URL}/api/convex-token`,
      { credentials: 'include' }
    );
    if (response.ok) {
      const { token } = await response.json();
      return token;
    }
    return null;
  } catch (error) {
    console.error('Failed to fetch Convex token:', error);
    return null;
  }
}

export function ConvexClientProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Pass the fetch function directly — Convex calls it when it needs a token
    // and waits for it before executing queries, eliminating the auth race condition.
    convex.setAuth(fetchConvexToken);

    return () => {
      convex.clearAuth();
    };
  }, []);

  return <ConvexProvider client={convex}>{children}</ConvexProvider>;
}

// Export the client for use in logout
export { convex };
