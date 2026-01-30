'use client';

import { useEffect, useState } from 'react';
import { useLoading } from '@/context/LoadingContext';
import Image from 'next/image';

// Animation Timing Constants (in ms)
// You can tune these to adjust the animation feel
const VIDEO_EXIT_DURATION_MS = 200;
const HOLE_REVEAL_DELAY_MS = 100;
const HOLE_REVEAL_DURATION_MS = 400;

// Calculated total duration to ensure cleanup happens after all animations are done
const TOTAL_EXIT_DURATION_MS = Math.max(
  VIDEO_EXIT_DURATION_MS, 
  HOLE_REVEAL_DELAY_MS + HOLE_REVEAL_DURATION_MS
);

// Toggle this to true to add an extra delay before the loading screen disappears
const DEBUG_DELAY = false;
const DEBUG_DELAY_MS = 0;

export function LoadingScreen() {
  const { isLoading } = useLoading();
  const [isMounted, setIsMounted] = useState(false);
  const [isSafari, setIsSafari] = useState(false);
  
  // Animation States
  const [videoScale, setVideoScale] = useState(1);
  const [holeSize, setHoleSize] = useState<'closed' | 'open'>('closed');
  const [isExiting, setIsExiting] = useState(false);

  useEffect(() => {
    // Detect Safari
    // Chrome on iOS also has "Safari" in userAgent, but it also has "CriOS"
    // Standard Safari: "Safari" in UA, "Chrome" NOT in UA
    const ua = navigator.userAgent;
    const isSafariDetected = ua.includes('Safari') && !ua.includes('Chrome') && !ua.includes('CriOS') && !ua.includes('FxiOS');
    setIsSafari(isSafariDetected);
  }, []);

  useEffect(() => {
    if (isLoading) {
      // RESET STATE (Enter Loading)
      setIsMounted(true);
      setIsExiting(false);
      setVideoScale(1);
      setHoleSize('closed');
    } else {
      // EXIT SEQUENCE (Finish Loading)
      if (isMounted && !isExiting) {
        
        const startExitSequence = () => {
          setIsExiting(true);

          // Step 1: Scale Video Down
          setVideoScale(0);

          // Step 2: Expand Hole
          const maskTimer = setTimeout(() => {
            setHoleSize('open');
          }, HOLE_REVEAL_DELAY_MS);

          // Step 3: Cleanup
          const cleanupTimer = setTimeout(() => {
            setIsMounted(false);
            setIsExiting(false);
            // Reset for next time
            setVideoScale(1);
            setHoleSize('closed');
          }, TOTAL_EXIT_DURATION_MS);

          return () => {
            clearTimeout(maskTimer);
            clearTimeout(cleanupTimer);
          };
        };

        if (DEBUG_DELAY) {
          const debugTimer = setTimeout(startExitSequence, DEBUG_DELAY_MS);
          return () => clearTimeout(debugTimer);
        } else {
          startExitSequence();
        }
      }
    }
  }, [isLoading, isMounted, isExiting]);

  if (!isMounted) return null;

  return (
    <div
      className={`fixed inset-0 z-[100] flex items-center justify-center overflow-hidden ${
        isExiting ? 'pointer-events-none' : 'pointer-events-auto'
      }`}
    >
      {/* 
        Background Layer using Box Shadow Trick 
        - The div is the "hole".
        - The shadow is the "curtain" (green).
        - We animate width/height instead of transform:scale to ensure shadow renders correctly.
        - Initial: w-0 h-0 (Hole closed, shadow covers all)
        - Final: w-[200vmax] (Hole open, shadow pushed out)
      */}
      <div 
        className="absolute left-1/2 top-1/2 rounded-full bg-transparent shadow-[0_0_0_300vmax_#166534]"
        style={{
          width: holeSize === 'closed' ? '0px' : '200vmax',
          height: holeSize === 'closed' ? '0px' : '200vmax',
          transform: 'translate(-50%, -50%)',
          transition: `width ${HOLE_REVEAL_DURATION_MS}ms ease-out, height ${HOLE_REVEAL_DURATION_MS}ms ease-out`,
        }}
      />

      {/* Loading Animation */}
      <div
        className="relative z-10 flex items-center justify-center"
        style={{
          width: '512px',
          height: '512px',
          transform: `scale(${videoScale})`,
          transition: `transform ${VIDEO_EXIT_DURATION_MS}ms ease-out`,
        }}
      >
        {isSafari ? (
          // Safari Fallback: GIF
          <Image
            src="/animations/loading.gif"
            alt="Loading..."
            width={512}
            height={512}
            className="object-contain"
            priority
            unoptimized // GIFs often need unoptimized to animate correctly in Next.js
          />
        ) : (
          // Default: Transparent Video
          <video
            loop
            autoPlay
            muted
            playsInline
            className="w-full h-full object-contain"
            width={512}
            height={512}
          >
            {/* Chrome / Firefox / Edge (WebM) */}
            <source src="/animations/loading.webm" type="video/webm" />
          </video>
        )}
      </div>
    </div>
  );
}
