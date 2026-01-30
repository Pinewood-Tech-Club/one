'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { AppLayout } from './AppLayout';
import { OnboardingController } from './onboarding/OnboardingController';

type AuthState = {
  isAuthenticated: boolean;
  onboardingStep: string | null;
} | null;

export function LayoutWrapper({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [authState, setAuthState] = useState<AuthState>(null);

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
        if (response.ok) {
          const data = await response.json();
          setAuthState({
            isAuthenticated: true,
            onboardingStep: data.onboarding_step || null,
          });
        } else {
          setAuthState({ isAuthenticated: false, onboardingStep: null });
        }
      } catch {
        setAuthState({ isAuthenticated: false, onboardingStep: null });
      }
    };

    checkAuth();
  }, [pathname]);

  // Bypass auth check for help/docs routes
  if (pathname.startsWith('/help')) {
    return <>{children}</>;
  }

  // Don't show anything while checking auth
  if (authState === null) {
    return null;
  }

  // If authenticated
  if (authState.isAuthenticated) {
    // Check if onboarding is incomplete
    if (authState.onboardingStep && authState.onboardingStep !== 'completed') {
      return <OnboardingController />;
    }
    // Onboarding complete, show AppLayout
    return <AppLayout />;
  }

  // Not authenticated, render children (home page)
  return <>{children}</>;
}
