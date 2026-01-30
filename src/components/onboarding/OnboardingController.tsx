'use client';

import { useEffect } from 'react';
import { useQuery } from 'convex/react';
import { api } from '../../../convex/_generated/api';
import { WelcomeStep } from './WelcomeStep';
import { ConnectLmsStep } from './ConnectLmsStep';
import { SmartConsentStep } from './SmartConsentStep';

export function OnboardingController() {
  const user = useQuery(api.users.getUser);

  // Redirect to dashboard when onboarding completes
  useEffect(() => {
    if (user?.onboardingStep === 'completed') {
      window.location.href = '/dashboard';
    }
  }, [user?.onboardingStep]);

  // Loading state
  if (user === undefined) {
    return (
      <div className="fixed inset-0 bg-[#1b8f4b] flex items-center justify-center">
        <div className="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin" />
      </div>
    );
  }

  if (user === null) {
    return null;
  }

  switch (user.onboardingStep) {
    case 'welcome':
      return <WelcomeStep />;
    case 'connect_lms':
      return <ConnectLmsStep />;
    case 'smart_consent':
      return <SmartConsentStep />;
    case 'completed':
      // Show loading while redirecting
      return (
        <div className="fixed inset-0 bg-[#7c3aed] flex items-center justify-center">
          <div className="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin" />
        </div>
      );
    default:
      return <WelcomeStep />;
  }
}
