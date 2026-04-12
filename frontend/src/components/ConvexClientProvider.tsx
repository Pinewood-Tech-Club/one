'use client';

import { ConvexProvider, ConvexReactClient } from 'convex/react';
import { ReactNode, createContext, useContext, useState, useEffect } from 'react';

// Tracks whether Convex has successfully obtained an auth token at least once.
// Used to gate authenticated queries so they never fire unauthenticated.
const ConvexAuthReadyContext = createContext(false);
export function useConvexAuthReady() {
  return useContext(ConvexAuthReadyContext);
}

// Module-level listeners so the module-level setAuth callback can notify
// the React tree when auth is first established.
type AuthReadyListener = (ready: boolean) => void;
const authReadyListeners: AuthReadyListener[] = [];
let resolvedAuthReady: boolean | null = null; // null = still pending

async function fetchConvexToken(): Promise<string | null> {
  try {
    const response = await fetch(
      `${process.env.NEXT_PUBLIC_BACKEND_URL}/api/convex-token`,
      { credentials: 'include' }
    );
    if (response.ok) {
      const { token } = await response.json();
      if (resolvedAuthReady === null) {
        resolvedAuthReady = true;
        authReadyListeners.forEach((fn) => fn(true));
      }
      return token;
    }
    if (resolvedAuthReady === null) {
      resolvedAuthReady = false;
      authReadyListeners.forEach((fn) => fn(false));
    }
    return null;
  } catch (error) {
    console.error('Failed to fetch Convex token:', error);
    if (resolvedAuthReady === null) {
      resolvedAuthReady = false;
      authReadyListeners.forEach((fn) => fn(false));
    }
    return null;
  }
}

const convex = new ConvexReactClient(process.env.NEXT_PUBLIC_CONVEX_URL!);
// Set auth synchronously at module level so Convex has the token fetcher
// before any components mount and subscribe to queries.
convex.setAuth(fetchConvexToken);

export function ConvexClientProvider({ children }: { children: ReactNode }) {
  const [authReady, setAuthReady] = useState(() => resolvedAuthReady === true);

  useEffect(() => {
    // If the token fetch already completed before this component mounted, sync immediately.
    if (resolvedAuthReady !== null) {
      setAuthReady(resolvedAuthReady);
      return;
    }
    // Otherwise wait for the module-level callback.
    authReadyListeners.push(setAuthReady);
    return () => {
      const idx = authReadyListeners.indexOf(setAuthReady);
      if (idx !== -1) authReadyListeners.splice(idx, 1);
    };
  }, []);

  return (
    <ConvexAuthReadyContext.Provider value={authReady}>
      <ConvexProvider client={convex}>{children}</ConvexProvider>
    </ConvexAuthReadyContext.Provider>
  );
}

// Export the client for use in logout
export { convex };
