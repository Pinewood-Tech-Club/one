'use client';

import { useEffect } from 'react';
import { useQuery } from 'convex/react';
import { api } from '../../../convex/_generated/api';
import { WelcomeStep } from './WelcomeStep';
import { ConnectLmsStep } from './ConnectLmsStep';
import { SmartConsentStep } from './SmartConsentStep';
import { mobileBridge } from '@/lib/mobileBridge';

type OnboardingMode = 'web' | 'mobile';

interface OnboardingControllerProps {
  mode?: OnboardingMode;
}

export function OnboardingController({ mode = 'web' }: OnboardingControllerProps) {
  const user = useQuery(api.users.getUser);

  // Redirect to dashboard when onboarding completes
  useEffect(() => {
    if (user?.onboardingStep !== 'completed') {
      return;
    }

    let cancelled = false;
    const finalizeOnboarding = async () => {
      if (mode === 'mobile') {
        const bridged = await mobileBridge.onboardingComplete();
        if (bridged || cancelled) {
          return;
        }
      }
      window.location.href = '/dashboard';
    };

    void finalizeOnboarding();
    return () => {
      cancelled = true;
    };
  }, [mode, user?.onboardingStep]);

  // Loading state
  if (user === undefined) {
    return (
      <div className="fixed inset-0 bg-[#1b8f4b] flex items-center justify-center">
        <div className="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin" />
      </div>
    );
  }

  if (user === null) {
    if (mode === 'mobile') {
      return (
        <div className="fixed inset-0 bg-[#0f172a] text-white flex items-center justify-center px-6">
          <div className="max-w-md text-center space-y-4">
            <h1 className="text-3xl font-semibold tracking-tight">Session Expired</h1>
            <p className="text-base opacity-80">
              This onboarding session is no longer authenticated. Return to the app and restart onboarding.
            </p>
            <button
              onClick={() => {
                window.location.href = '/';
              }}
              className="px-8 py-3 bg-white text-slate-900 rounded-full font-semibold text-base shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-200 cursor-pointer"
            >
              Return Home
            </button>
          </div>
        </div>
      );
    }
    return null;
  }

  switch (user.onboardingStep) {
    case 'welcome':
      return <WelcomeStep />;
    case 'connect_lms':
      return <ConnectLmsStep mode={mode} />;
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
