'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { AppLayout } from './AppLayout';

export function LayoutWrapper({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);

  useEffect(() => {
    // Skip auth check for help/docs routes
    if (pathname.startsWith('/help')) {
      return;
    }

    const checkAuth = async () => {
      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/user`, {
          credentials: 'include',
        });
        setIsAuthenticated(response.ok);
      } catch {
        setIsAuthenticated(false);
      }
    };

    checkAuth();
  }, [pathname]);

  // Bypass auth check for help/docs routes
  if (pathname.startsWith('/help')) {
    return <>{children}</>;
  }

  // Don't show anything while checking auth
  if (isAuthenticated === null) {
    return null;
  }

  // If authenticated, show AppLayout (which handles all pages internally)
  if (isAuthenticated) {
    return <AppLayout />;
  }

  // Not authenticated, render children (home page)
  return <>{children}</>;
}
