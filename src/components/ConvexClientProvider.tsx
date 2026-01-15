'use client';

import { ConvexProvider, ConvexReactClient } from 'convex/react';
import { ReactNode, useEffect, useCallback, useRef } from 'react';

const convex = new ConvexReactClient(process.env.NEXT_PUBLIC_CONVEX_URL!);

export function ConvexClientProvider({ children }: { children: ReactNode }) {
  const isAuthSet = useRef(false);

  const fetchAndSetToken = useCallback(async () => {
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_BACKEND_URL}/api/convex-token`,
        { credentials: 'include' }
      );

      if (response.ok) {
        const { token } = await response.json();
        // Set the auth token for Convex
        convex.setAuth(async () => token);
        isAuthSet.current = true;
      } else {
        // User is not authenticated, clear any existing auth
        convex.clearAuth();
        isAuthSet.current = false;
      }
    } catch (error) {
      console.error('Failed to fetch Convex token:', error);
      convex.clearAuth();
      isAuthSet.current = false;
    }
  }, []);

  useEffect(() => {
    // Fetch token on mount
    fetchAndSetToken();

    // Refresh token every 20 minutes (tokens expire in 24 hours)
    const refreshInterval = setInterval(fetchAndSetToken, 20 * 60 * 1000);

    return () => {
      clearInterval(refreshInterval);
    };
  }, [fetchAndSetToken]);

  return <ConvexProvider client={convex}>{children}</ConvexProvider>;
}

// Export the client for use in logout
export { convex };
