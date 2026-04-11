'use client';

import { useEffect, useState } from 'react';

interface OnboardingSlideProps {
  backgroundColor: string;
  children: React.ReactNode;
}

export function OnboardingSlide({ backgroundColor, children }: OnboardingSlideProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // Trigger fade-in after mount
    const timer = setTimeout(() => setMounted(true), 50);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div
      className="fixed inset-0 flex flex-col items-center justify-center text-center px-6 text-white transition-all duration-700 ease-out"
      style={{ backgroundColor }}
    >
      <div
        className={`transition-all duration-500 ease-out ${
          mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'
        }`}
      >
        {children}
      </div>
    </div>
  );
}
