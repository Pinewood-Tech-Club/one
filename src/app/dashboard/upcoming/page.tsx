'use client';

import { useTimeBasedGreeting } from '@/hooks/useTimeBasedGreeting';
import { useUser } from '@/context/UserContext';
import Image from 'next/image';

export default function UpcomingPage() {
  const { userName } = useUser();
  const greeting = useTimeBasedGreeting(userName || 'there');

  return (
    <div>
      <div className="p-4 h-64 flex flex-col justify-end">
        <div className="absolute top-0 left-0 w-full h-64 z-[-1]">
          {/* fill with /public/banner-images/afternoon/001.webp */}
          {/* <img src="/banner-photos/afternoon/001.webp" alt="Banner" className="w-full h-full object-cover object-center" /> */}
          <Image src="/banner-photos/afternoon/001.webp" alt="Banner" fill className="object-cover object-center" priority />
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold text-white">{greeting}</h1>
      </div>
      <div>
        <h1 className="text-3xl font-bold p-4 text-green-800 dark:text-green-400">Upcoming Assignments</h1>
      </div>
    </div>
  );
}

