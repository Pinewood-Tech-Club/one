'use client';

import { useState } from 'react';
import { useQuery } from 'convex/react';
import { api } from '../../../../convex/_generated/api';
import { useTimeBasedGreeting } from '@/hooks/useTimeBasedGreeting';
import { useUser } from '@/context/UserContext';
import Image from 'next/image';
import { UpcomingAssignmentsCarosuel } from '@/components/upcoming/UpcomingAssignmentsCarosuel';
import { IconWrapper } from '@/components/icons/IconWrapper';
import posthog from 'posthog-js';

export default function UpcomingPage() {
  const { userName } = useUser();
  const greeting = useTimeBasedGreeting(userName || 'there');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const upcomingAssignments = useQuery(api.schoologyCache.getUpcoming);

  const handleRefresh = async () => {
    if (isRefreshing) return;

    // Track refresh button click
    posthog.capture('refresh_clicked');

    setIsRefreshing(true);
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/schoology/refresh`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!response.ok) {
        console.error('Failed to refresh Schoology data');
      }
    } catch (error) {
      console.error('Error refreshing Schoology data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <div>
      <div className="p-4 h-64 flex flex-col justify-end">
        <div className="absolute top-0 left-0 w-full h-64 z-[-1]">
          {/* fill with /public/banner-images/afternoon/001.webp */}
          {/* <img src="/banner-photos/afternoon/001.webp" alt="Banner" className="w-full h-full object-cover object-center" /> */}
          <Image src="/banner-photos/afternoon/001.webp" alt="Banner" fill className="object-cover object-center" priority />
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold text-white" data-ph-mask>{greeting}</h1>
      </div>
      {(upcomingAssignments === undefined || upcomingAssignments.length > 0) && (
        <div>
          <div className="flex justify-between items-center">
            <h1 className="text-3xl font-bold p-4 pb-0 text-green-800 dark:text-green-400">Upcoming Assignments</h1>
            <div className="flex items-center justify-end gap-2 p-4 pb-0">
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
                aria-label="Refresh Schoology data"
              >
                <IconWrapper
                  src="/icons/refresh.svg"
                  alt="Refresh Schoology"
                  className={`w-8 h-8 text-green-800 dark:text-green-400 transition-transform duration-500 ${isRefreshing ? 'animate-spin' : 'hover:rotate-180'}`}
                  color='currentColor'
                />
              </button>
            </div>
          </div>
          <UpcomingAssignmentsCarosuel />
        </div>
      )}
    </div>
  );
}

