'use client';

import { useTimeBasedGreeting } from '@/hooks/useTimeBasedGreeting';
import { useUser } from '@/context/UserContext';

export default function UpcomingPage() {
  const { userName } = useUser();
  const greeting = useTimeBasedGreeting(userName || 'there');

  return (
    <div>
      <h1 className="text-3xl font-bold p-4">{greeting}</h1>
    </div>
  );
}

