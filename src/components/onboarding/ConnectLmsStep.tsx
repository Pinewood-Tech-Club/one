'use client';

import { useSearchParams } from 'next/navigation';
import { OnboardingSlide } from './OnboardingSlide';

const BACKGROUND_COLOR = '#2563eb';

export function ConnectLmsStep() {
  const searchParams = useSearchParams();
  const error = searchParams.get('error');

  const handleConnect = () => {
    window.location.href = `${process.env.NEXT_PUBLIC_BACKEND_URL}/oauth/schoology/start`;
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
      <button
        onClick={handleConnect}
        className="px-10 py-4 bg-white text-blue-600 rounded-full font-semibold text-lg shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-200 cursor-pointer"
      >
        Connect Schoology
      </button>
    </OnboardingSlide>
  );
}
