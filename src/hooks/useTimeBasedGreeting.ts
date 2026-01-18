'use client';

import { useState, useEffect } from 'react';

function getGreetingByHour(hour: number): string {
  // 5:00 AM to 12:00 PM
  if (hour >= 5 && hour < 12) {
    return 'morning';
  }
  // 12:00 PM to 4:00 PM
  if (hour >= 12 && hour < 16) {
    return 'afternoon';
  }
  // 4:00 PM to 8:00 PM
  if (hour >= 16 && hour < 20) {
    return 'evening';
  }
  // 8:00 PM to 1:00 AM
  if (hour >= 20 || hour < 1) {
    return 'night';
  }
  // 1:00 AM to 5:00 AM
  return 'sleep';
}

function formatGreeting(type: string, name: string): string {
  switch (type) {
    case 'morning':
      return `Good morning, ${name}`;
    case 'afternoon':
      return `Good afternoon, ${name}`;
    case 'evening':
      return `Good evening, ${name}`;
    case 'night':
      return `Good night, ${name}`;
    case 'sleep':
      return `Go to sleep, ${name}`;
    default:
      return `Hello, ${name}`;
  }
}

export function useTimeBasedGreeting(name: string): string {
  const [greeting, setGreeting] = useState<string>('');

  useEffect(() => {
    let timeoutId: NodeJS.Timeout | null = null;
    let isActive = true;

    // Update greeting function
    const updateGreeting = () => {
      const now = new Date();
      const hour = now.getHours();
      const greetingType = getGreetingByHour(hour);
      setGreeting(formatGreeting(greetingType, name));
    };

    // Set initial greeting
    updateGreeting();

    // Calculate time until next greeting change
    const calculateTimeUntilNextChange = (): number => {
      const now = new Date();
      const currentHour = now.getHours();

      // Greeting changes at 1 AM, 5 AM, 12 PM, 4 PM, 8 PM
      const greetingHours = [1, 5, 12, 16, 20];

      let nextChangeHour = greetingHours.find(h => h > currentHour);
      if (!nextChangeHour) {
        // Next change is tomorrow at 1 AM
        nextChangeHour = greetingHours[0];
      }

      const nextChange = new Date();
      nextChange.setHours(nextChangeHour, 0, 0, 0);

      if (nextChange <= now) {
        nextChange.setDate(nextChange.getDate() + 1);
      }

      return nextChange.getTime() - now.getTime();
    };

    // Set up timeout to update at the next greeting change
    const scheduleNextUpdate = () => {
      if (!isActive) return;
      const timeUntilChange = calculateTimeUntilNextChange();
      timeoutId = setTimeout(() => {
        updateGreeting();
        scheduleNextUpdate();
      }, timeUntilChange);
    };

    scheduleNextUpdate();

    return () => {
      isActive = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [name]);

  return greeting;
}
