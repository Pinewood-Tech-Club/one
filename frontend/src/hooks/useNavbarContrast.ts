'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

interface NavbarContrastOptions {
  navbarHeight?: number;
  samplePoints?: number;
  scrollThrottleMs?: number;
  idleIntervalMs?: number;
  luminosityThreshold?: number;
  /** When this value changes, immediately re-sample (e.g., pass currentPage) */
  trigger?: unknown;
}

/**
 * Hook that samples background colors under the navbar region and determines
 * if the text should be light or dark based on average luminosity.
 */
export function useNavbarContrast(options: NavbarContrastOptions = {}) {
  const {
    navbarHeight = 56,
    samplePoints = 10,
    scrollThrottleMs = 100,
    idleIntervalMs = 500,
    luminosityThreshold = 128,
    trigger,
  } = options;

  const [isOverDark, setIsOverDark] = useState(false);
  const lastSampleTime = useRef(0);
  const imageCanvasCache = useRef<Map<string, HTMLCanvasElement>>(new Map());

  // Calculate luminosity from RGB using relative luminance formula
  const getLuminosity = (r: number, g: number, b: number) =>
    0.299 * r + 0.587 * g + 0.114 * b;

  // Parse CSS color string to RGB values
  const parseColor = useCallback((color: string): [number, number, number] | null => {
    if (color === 'transparent' || color === 'rgba(0, 0, 0, 0)') return null;
    const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (match) return [+match[1], +match[2], +match[3]];
    return null;
  }, []);

  // Sample color at a specific screen coordinate
  const samplePointColor = useCallback((x: number, y: number): [number, number, number] | null => {
    const elements = document.elementsFromPoint(x, y);

    for (const el of elements) {
      // Skip the navbar itself to sample what's behind it
      if (el.closest('[data-navbar]')) continue;

      const style = getComputedStyle(el);

      // Check for background image (handle banner images)
      const bgImage = style.backgroundImage;
      if (bgImage && bgImage !== 'none') {
        // Extract URL and sample from cached canvas
        const urlMatch = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
        if (urlMatch) {
          const canvas = imageCanvasCache.current.get(urlMatch[1]);
          if (canvas) {
            const rect = el.getBoundingClientRect();
            // Calculate position within the background image
            const bgX = Math.max(0, Math.min(canvas.width - 1,
              ((x - rect.left) / rect.width) * canvas.width));
            const bgY = Math.max(0, Math.min(canvas.height - 1,
              ((y - rect.top) / rect.height) * canvas.height));
            const ctx = canvas.getContext('2d');
            if (ctx) {
              try {
                const pixel = ctx.getImageData(Math.floor(bgX), Math.floor(bgY), 1, 1).data;
                return [pixel[0], pixel[1], pixel[2]];
              } catch {
                // Canvas tainted by CORS, fall through to next element
              }
            }
          }
        }
      }

      // Check for solid background color
      const bgColor = parseColor(style.backgroundColor);
      if (bgColor) return bgColor;
    }

    // Default to white (assume light background)
    return [255, 255, 255];
  }, [parseColor]);

  // Pre-load a background image into canvas for later sampling
  const cacheBackgroundImage = useCallback((url: string) => {
    if (imageCanvasCache.current.has(url)) return;

    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.drawImage(img, 0, 0);
        imageCanvasCache.current.set(url, canvas);
      }
    };
    img.onerror = () => {
      // If image fails to load, we'll fall back to solid color detection
    };
    img.src = url;
  }, []);

  // Core sampling logic (no throttling)
  const doSample = useCallback(() => {
    const y = navbarHeight / 2;
    const luminosities: number[] = [];

    for (let i = 0; i < samplePoints; i++) {
      // Distribute sample points evenly across viewport width (5% to 95%)
      const x = window.innerWidth * (0.05 + (i * 0.9) / (samplePoints - 1));
      const color = samplePointColor(x, y);
      if (color) {
        luminosities.push(getLuminosity(...color));
      }
    }

    if (luminosities.length > 0) {
      const avgLuminosity = luminosities.reduce((a, b) => a + b, 0) / luminosities.length;
      setIsOverDark(avgLuminosity < luminosityThreshold);
    }
  }, [navbarHeight, samplePoints, luminosityThreshold, samplePointColor]);

  // Throttled sampling function for scroll events
  const sampleNavbarBackground = useCallback(() => {
    const now = Date.now();
    if (now - lastSampleTime.current < scrollThrottleMs) return;
    lastSampleTime.current = now;
    doSample();
  }, [scrollThrottleMs, doSample]);

  useEffect(() => {
    // SSR guard - only run on client
    if (typeof window === 'undefined') return;

    // Scan for background images and cache them
    const scanForImages = () => {
      document.querySelectorAll('*').forEach(el => {
        const bgImage = getComputedStyle(el).backgroundImage;
        if (bgImage && bgImage !== 'none') {
          const urlMatch = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
          if (urlMatch) cacheBackgroundImage(urlMatch[1]);
        }
      });
    };

    // Initial scan and sample
    scanForImages();
    // Small delay to allow images to start loading
    const initialTimeout = setTimeout(sampleNavbarBackground, 100);

    // Scroll listener with passive flag for performance
    const handleScroll = () => {
      requestAnimationFrame(sampleNavbarBackground);
    };

    // Idle interval for catching dynamic content changes
    const idleInterval = setInterval(sampleNavbarBackground, idleIntervalMs);

    window.addEventListener('scroll', handleScroll, { passive: true });

    return () => {
      window.removeEventListener('scroll', handleScroll);
      clearInterval(idleInterval);
      clearTimeout(initialTimeout);
    };
  }, [sampleNavbarBackground, cacheBackgroundImage, idleIntervalMs]);

  // Immediately re-sample when trigger changes (e.g., page/tab switch)
  useEffect(() => {
    if (typeof window === 'undefined') return;
    // Small delay to allow new page content to render
    const timeout = setTimeout(doSample, 50);
    return () => clearTimeout(timeout);
  }, [trigger, doSample]);

  return isOverDark;
}
