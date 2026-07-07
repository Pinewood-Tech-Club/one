'use client';

import { useCallback, useEffect, useState } from 'react';
import { useLiveQuery } from '@/hooks/useLiveQuery';
import { getUser, type ApiUser, type OnboardingStep } from '@/lib/api';
import { WelcomeStep } from './WelcomeStep';
import { ConnectLmsStep } from './ConnectLmsStep';
import { SmartConsentStep } from './SmartConsentStep';
import { mobileBridge } from '@/lib/mobileBridge';

type OnboardingMode = 'web' | 'mobile';

interface OnboardingControllerProps {
  mode?: OnboardingMode;
}

export function OnboardingController({ mode = 'web' }: OnboardingControllerProps) {
  // The user.updated event covers the Schoology-OAuth path (full-page redirect)
  // and other tabs; handleUserUpdate advances synchronously from a mutation's
  // own response so a click never waits on the event round-trip.
  const { data, error } = useLiveQuery<ApiUser>({
    fetcher: getUser,
    events: [{ type: 'user.updated', apply: (d) => (d.user ?? d) as ApiUser }],
  });
  const [user, setUser] = useState<ApiUser | undefined>(undefined);
  const [completionBridgeError, setCompletionBridgeError] = useState<string | null>(null);

  useEffect(() => {
    if (data) setUser(data);
  }, [data]);

  const handleUserUpdate = useCallback((updated: ApiUser) => {
    setUser(updated);
  }, []);

  const currentStep: OnboardingStep | null | undefined = user?.onboarding_step ?? null;

  const finalizeOnboarding = useCallback(async () => {
    if (mode === 'mobile') {
      const bridgeResult = await mobileBridge.onboardingCompleteDetailed();
      if (bridgeResult.status === 'invoked') {
        setCompletionBridgeError(null);
        return;
      }
      if (bridgeResult.status === 'error') {
        setCompletionBridgeError(
          bridgeResult.error || 'Could not hand control back to the app. Please try again.'
        );
        return;
      }
    }
    setCompletionBridgeError(null);
    window.location.href = '/';
  }, [mode]);

  // Redirect to dashboard when onboarding completes
  useEffect(() => {
    if (currentStep !== 'completed') {
      return;
    }

    let cancelled = false;
    const runFinalize = async () => {
      await finalizeOnboarding();
      if (cancelled) {
        return;
      }
    };

    void runFinalize();
    return () => {
      cancelled = true;
    };
  }, [currentStep, finalizeOnboarding]);

  // Loading state
  if (user === undefined && !error) {
    return (
      <div className="fixed inset-0 bg-[#1b8f4b] flex items-center justify-center">
        <div className="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin" />
      </div>
    );
  }

  // Unauthenticated / fetch failure.
  if (user === undefined) {
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

  switch (currentStep) {
    case 'welcome':
      return <WelcomeStep onUserUpdate={handleUserUpdate} />;
    case 'connect_lms':
      return <ConnectLmsStep mode={mode} onUserUpdate={handleUserUpdate} />;
    case 'smart_consent':
      return <SmartConsentStep onUserUpdate={handleUserUpdate} />;
    case 'completed':
      if (mode === 'mobile' && completionBridgeError) {
        return (
          <div className="fixed inset-0 bg-[#7c3aed] text-white flex items-center justify-center px-6">
            <div className="max-w-md text-center space-y-5">
              <h1 className="text-3xl font-semibold tracking-tight">Couldn&apos;t Finish Onboarding</h1>
              <p className="text-base opacity-90">{completionBridgeError}</p>
              <div className="flex flex-col sm:flex-row gap-3 justify-center">
                <button
                  onClick={() => {
                    void finalizeOnboarding();
                  }}
                  className="px-7 py-3 bg-white text-violet-800 rounded-full font-semibold text-base shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-200 cursor-pointer"
                >
                  Retry
                </button>
                <button
                  onClick={() => {
                    window.location.href = '/';
                  }}
                  className="px-7 py-3 bg-white/20 text-white rounded-full font-semibold text-base border border-white/35 hover:bg-white/30 transition-all duration-200 cursor-pointer"
                >
                  Continue in Browser
                </button>
              </div>
            </div>
          </div>
        );
      }
      // Show loading while redirecting
      return (
        <div className="fixed inset-0 bg-[#7c3aed] flex items-center justify-center">
          <div className="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin" />
        </div>
      );
    default:
      return <WelcomeStep onUserUpdate={handleUserUpdate} />;
  }
}
