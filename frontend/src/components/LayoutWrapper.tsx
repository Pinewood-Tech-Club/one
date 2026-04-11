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
  const isHelpRoute = pathname.startsWith('/help');
  const isMobileOnboardingRoute = pathname.startsWith('/mobile/onboarding');
  const shouldBypassAuthGate = isHelpRoute || isMobileOnboardingRoute;

  useEffect(() => {
    // Skip auth check for help/docs and mobile onboarding routes.
    // Mobile onboarding has its own auth/session handling inside the page.
    if (shouldBypassAuthGate) {
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
  }, [pathname, shouldBypassAuthGate]);

  // Bypass auth check for help/docs and mobile onboarding routes.
  if (shouldBypassAuthGate) {
    return <>{children}</>;
  }

  // Don't show anything while checking auth
  if (authState === null) {
    return null;
  }

  // If authenticated
  if (authState.isAuthenticated) {
    if (isMobileOnboardingRoute) {
      return <>{children}</>;
    }
    // Check if onboarding is incomplete
    if (authState.onboardingStep && authState.onboardingStep !== 'completed') {
      return <OnboardingController />;
    }
    // Onboarding complete, show AppLayout
    return <AppLayout />;
  }

  // Not authenticated — redirect to login for known protected routes
  const isProtectedRoute =
    pathname === '/upcoming' ||
    pathname === '/schedule' ||
    pathname === '/grades' ||
    pathname.startsWith('/chat') ||
    pathname === '/user' ||
    pathname.startsWith('/onboarding') ||
    pathname.startsWith('/dashboard');

  if (isProtectedRoute) {
    window.location.replace('/login');
    return null;
  }

  return <>{children}</>;
}
