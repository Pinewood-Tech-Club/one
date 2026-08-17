'use client';

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { OnboardingSlide } from './OnboardingSlide';
import { mobileBridge } from '@/lib/mobileBridge';
import { ApiError, postDeveloperOverride, type ApiUser } from '@/lib/api';

const BACKGROUND_COLOR = '#2563eb';

type OnboardingMode = 'web' | 'mobile';

interface ConnectLmsStepProps {
  mode?: OnboardingMode;
  onUserUpdate: (user: ApiUser) => void;
}

export function ConnectLmsStep({ mode = 'web', onUserUpdate }: ConnectLmsStepProps) {
  const searchParams = useSearchParams();
  const error = searchParams.get('error');
  const [bridgeError, setBridgeError] = useState<string | null>(null);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideClientId, setOverrideClientId] = useState('');
  const [overrideClientSecret, setOverrideClientSecret] = useState('');
  const [overrideSubmitting, setOverrideSubmitting] = useState(false);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleConnect = async () => {
    setBridgeError(null);

    if (mode === 'mobile') {
      const bridgeResult = await mobileBridge.startSchoologyOAuthDetailed();
      if (bridgeResult.status === 'invoked') {
        return;
      }
      if (bridgeResult.status === 'error') {
        setBridgeError(
          bridgeResult.error || 'Could not start Schoology sign-in from the app. Please try again.'
        );
        return;
      }
    }

    // Deterministic fallback for non-bridge environments.
    window.location.href = `${process.env.NEXT_PUBLIC_BACKEND_URL}/oauth/schoology/start`;
  };

  const handleOverrideSubmit = async () => {
    setOverrideSubmitting(true);
    setOverrideError(null);
    try {
      const data = await postDeveloperOverride({
        clientId: overrideClientId,
        clientSecret: overrideClientSecret,
      });
      if (!mountedRef.current) return;
      // Advance instantly from the response; the user.updated event also fires.
      onUserUpdate(data.user);
      setOverrideOpen(false);
      setOverrideClientId('');
      setOverrideClientSecret('');
    } catch (e) {
      if (!mountedRef.current) return;
      const message =
        e instanceof ApiError && e.code ? e.code : 'Something went wrong. Please try again.';
      setOverrideError(message);
    } finally {
      if (mountedRef.current) setOverrideSubmitting(false);
    }
  };

  return (
    <OnboardingSlide backgroundColor={BACKGROUND_COLOR}>
      <h1 className="text-6xl sm:text-7xl font-bold tracking-tight mb-6">
        Connect Schoology
      </h1>
      <p className="text-xl opacity-80 max-w mb-10 leading-relaxed">
        Link your account to sync courses, grades, and assignments.
      </p>
      {error && (
        <p className="bg-white/20 px-5 py-3 rounded-xl mb-8 text-base">
          {error === 'access_denied'
            ? 'Connection was cancelled. Please try again.'
            : 'Something went wrong. Please try again.'}
        </p>
      )}
      {bridgeError && (
        <p className="bg-red-500/20 border border-red-200/40 px-5 py-3 rounded-xl mb-8 text-base">
          {bridgeError}
        </p>
      )}
      <button
        onClick={() => {
          void handleConnect();
        }}
        className="px-10 py-4 bg-white text-blue-600 rounded-full font-semibold text-lg shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-200 cursor-pointer"
      >
        Connect Schoology
      </button>

      <button
        type="button"
        onClick={() => setOverrideOpen(true)}
        className="fixed bottom-3 right-3 text-[8px] cursor-default"
        style={{ color: BACKGROUND_COLOR }}
        aria-hidden="true"
        tabIndex={-1}
      >
        developer override
      </button>

      {overrideOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-6">
          <button
            type="button"
            onClick={() => setOverrideOpen(false)}
            className="absolute inset-0"
            aria-label="Close"
          />
          <div className="relative w-full max-w-sm rounded-2xl bg-white text-black shadow-2xl p-5">
            <div className="space-y-3">
              <label className="sr-only" htmlFor="dev-override-client-id">
                Client ID
              </label>
              <input
                id="dev-override-client-id"
                value={overrideClientId}
                onChange={(e) => setOverrideClientId(e.target.value)}
                className="w-full rounded-xl border border-black/10 px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              <label className="sr-only" htmlFor="dev-override-client-secret">
                Client Secret
              </label>
              <input
                id="dev-override-client-secret"
                value={overrideClientSecret}
                onChange={(e) => setOverrideClientSecret(e.target.value)}
                className="w-full rounded-xl border border-black/10 px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                type="password"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              {overrideError && (
                <p className="text-sm text-red-600">{overrideError}</p>
              )}
            </div>

            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setOverrideOpen(false)}
                className="px-4 py-2 rounded-full text-sm font-semibold text-black/70 hover:text-black transition-colors"
              >
                Dismiss
              </button>
              <button
                type="button"
                onClick={handleOverrideSubmit}
                disabled={overrideSubmitting}
                className="px-5 py-2 rounded-full text-sm font-semibold bg-blue-600 text-white disabled:opacity-60"
              >
                {overrideSubmitting ? 'Submitting...' : 'Submit'}
              </button>
            </div>
          </div>
        </div>
      )}
    </OnboardingSlide>
  );
}
