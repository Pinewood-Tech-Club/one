'use client';

import { useState } from 'react';
import { OnboardingSlide } from './OnboardingSlide';

const BACKGROUND_COLOR = '#1b8f4b';

export function WelcomeStep() {
  const [loading, setLoading] = useState(false);

  const handleGetStarted = async () => {
    setLoading(true);
    try {
      await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/user/onboarding/start`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch (error) {
      console.error('Failed to start onboarding:', error);
      setLoading(false);
    }
  };

  return (
    <OnboardingSlide backgroundColor={BACKGROUND_COLOR}>
      <p className="text-base text-xl tracking-wide opacity-80 mb-4">
        Welcome to
      </p>
      <h1 className="text-6xl sm:text-7xl font-bold tracking-tight mb-6">
        Pinewood One
      </h1>
      <p className="text-xl opacity-80 max-w mb-10 leading-relaxed">
        Your personalized dashboard for grades, assignments, and schedule.
      </p>
      <button
        onClick={handleGetStarted}
        disabled={loading}
        className="px-10 py-4 bg-white text-green-800 rounded-full font-semibold text-lg shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-200 disabled:opacity-60 disabled:scale-100 cursor-pointer"
      >
        {loading ? 'Starting...' : 'Get Started'}
      </button>
    </OnboardingSlide>
  );
}
