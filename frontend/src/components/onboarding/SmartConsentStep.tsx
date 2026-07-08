'use client';

import { useState } from 'react';
import { OnboardingSlide } from './OnboardingSlide';
import { saveConsent, type ApiUser } from '@/lib/api';

const BACKGROUND_COLOR = '#7c3aed';

interface SmartConsentStepProps {
  onUserUpdate: (user: ApiUser) => void;
}

export function SmartConsentStep({ onUserUpdate }: SmartConsentStepProps) {
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    setLoading(true);
    try {
      const response = await saveConsent({ enabled, version: '1.0' });
      onUserUpdate(response.user);
    } catch (error) {
      console.error('Failed to save consent:', error);
      setLoading(false);
    }
  };

  return (
    <OnboardingSlide backgroundColor={BACKGROUND_COLOR}>
      <p className="text-base font-medium tracking-wide uppercase opacity-80 mb-4">
        Step 3 of 3
      </p>
      <h1 className="text-6xl sm:text-7xl font-bold tracking-tight mb-6">
        Smart Features
      </h1>
      <p className="text-lg opacity-80 max-w mb-2 leading-relaxed">
        Get AI-powered insights and personalized recommendations.
      </p>
      <p className="text-lg opacity-80 max-w mb-2 leading-relaxed">
        This is completely optional and you can change your preference later.
      </p>
      <p className="text-lg opacity-80 max-w mb-4 leading-relaxed">
        We care about your privacy and use exclusively zero-data retention providers to process your data. To learn more, read our <a href="/help/privacy" className="underline font-semibold">Privacy Policy</a>.
      </p>
      <button
        onClick={() => setEnabled(!enabled)}
        className="flex items-center mb-10 justify-between transition-colors mx-auto w-[136px] cursor-pointer"
      >
        <div
          className={`relative w-14 h-8 rounded-full transition-colors duration-200 ${
            enabled ? 'bg-white' : 'bg-white/30'
          }`}
        >
          <span
            className={`absolute top-1 w-6 h-6 rounded-full transition-all duration-200 ${
              enabled ? 'left-7 bg-purple-600' : 'left-1 bg-white'
            }`}
          />
        </div>
        <span className="text-lg">
          {enabled ? 'Enabled' : 'Disabled'}
        </span>
      </button>
      <button
        onClick={handleSubmit}
        disabled={loading}
        className="px-10 py-4 bg-white text-purple-600 rounded-full font-semibold text-lg shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-200 disabled:opacity-60 disabled:scale-100 cursor-pointer"
      >
        {loading ? 'Finishing...' : 'Complete Setup'}
      </button>
    </OnboardingSlide>
  );
}
