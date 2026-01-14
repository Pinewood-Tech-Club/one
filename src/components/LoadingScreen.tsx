'use client';

import { useEffect, useState } from 'react';
import { useLoading } from '@/context/LoadingContext';

export function LoadingScreen() {
  const { isLoading } = useLoading();
  const [shouldRender, setShouldRender] = useState(false);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    if (isLoading) {
      // Loading started, show immediately
      setShouldRender(true);
      setIsVisible(true);
    } else if (shouldRender) {
      // Loading finished, start exit animation
      setIsVisible(false);
      // Wait for animation to complete before unmounting
      const timer = setTimeout(() => {
        setShouldRender(false);
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [isLoading, shouldRender]);

  if (!shouldRender) {
    return null;
  }

  return (
    <div
      className={`fixed top-0 left-0 w-full h-full bg-green-800 z-50 flex items-center justify-center transition-all duration-300 ease-in-out ${
        isVisible ? 'opacity-100 scale-100' : 'opacity-0 scale-[1.2]'
      }`}
    >
      <p className="text-white text-2xl">Loading...</p>
    </div>
  );
}
