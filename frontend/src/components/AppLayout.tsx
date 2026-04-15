'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { LoadingProvider, useLoading } from '@/context/LoadingContext';
import { UserProvider } from '@/context/UserContext';
import UpcomingPage from '@/app/dashboard/upcoming/page';
import SchedulePage from '@/app/dashboard/schedule/page';
import GradesPage from '@/app/dashboard/grades/page';
import ChatPage from '@/app/dashboard/chat/page';
import UserPage from '@/app/dashboard/user/page';
import { LoadingScreen } from '@/components/LoadingScreen';
import { NavPill } from '@/components/NavPill';
import { useQuery } from 'convex/react';
import { api } from '../../convex/_generated/api';
import posthog from 'posthog-js';
import { CalendarDays, ChartNoAxesColumnIncreasing, Flag, MessageCircleMore } from 'lucide-react';

type Page = 'upcoming' | 'progress' | 'activities' | 'chat' | 'user';

const NAV_PAGE_ORDER: Page[] = ['upcoming', 'progress', 'activities', 'chat'];

const NAV_ITEMS = [
  { page: 'upcoming'   as Page, label: 'Upcoming',   Icon: CalendarDays },
  { page: 'progress'   as Page, label: 'Progress',   Icon: ChartNoAxesColumnIncreasing },
  { page: 'activities' as Page, label: 'Activities', Icon: Flag },
  { page: 'chat'       as Page, label: 'Chat',       Icon: MessageCircleMore },
];

function pathnameToPage(pathname: string): Page {
  if (pathname === '/' || pathname === '/upcoming') return 'upcoming';
  if (pathname === '/progress') return 'progress';
  if (pathname === '/activities') return 'activities';
  if (pathname === '/chat' || pathname === '/chat/') return 'chat';
  if (pathname === '/user') return 'user';
  return 'upcoming';
}

function pageToPathname(page: Page): string {
  if (page === 'upcoming') return '/';
  return `/${page}`;
}

function AppLayoutInner() {
  const pathname = usePathname();
  const [userName, setUserName] = useState<string>('Loading...');
  const [userId, setUserId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState<Page>(() => pathnameToPage(pathname));
  const { setLoading } = useLoading();
  const convexUser = useQuery(api.users.getUser);

  // Sync pathname → page (browser back/forward)
  useEffect(() => {
    setCurrentPage(pathnameToPage(pathname));
  }, [pathname]);

  // Track tab switches
  useEffect(() => {
    posthog.capture('tab_switched', { tab: currentPage });
  }, [currentPage]);

  // Update URL when page changes
  useEffect(() => {
    const targetPath = pageToPathname(currentPage);
    if (pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  }, [currentPage, pathname]);

  useEffect(() => {
    const fetchUser = async () => {
      setLoading('user-fetch', true);
      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/user`, {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          setUserName(data.name);
          setUserId(data.email);
        }
      } catch (error) {
        console.error('Failed to fetch user:', error);
        setUserName('User');
      } finally {
        setLoading('user-fetch', false);
      }
    };
    fetchUser();
  }, [setLoading]);

  useEffect(() => {
    const refreshSchoologyData = async () => {
      setLoading('schoology-refresh', true);
      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/schoology/refresh`, {
          method: 'POST',
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          console.log('[Schoology] Auto-refresh completed:', data);
        } else {
          console.log('[Schoology] Auto-refresh skipped - not connected');
        }
      } catch (error) {
        console.error('[Schoology] Auto-refresh error:', error);
      } finally {
        setLoading('schoology-refresh', false);
      }
    };
    refreshSchoologyData();
  }, [setLoading]);

  const navSelectedIndex = NAV_PAGE_ORDER.indexOf(currentPage);

  const handleNavSelect = (index: number) => {
    setCurrentPage(NAV_PAGE_ORDER[index]);
  };

  return (
    <UserProvider userName={userName} userId={userId}>
      <div>
        {/* Nav pill — top left */}
        {currentPage !== 'user' && (
          <NavPill
            tabs={NAV_ITEMS}
            selectedIndex={navSelectedIndex === -1 ? 0 : navSelectedIndex}
            onSelect={handleNavSelect}
          />
        )}

        {/* Profile picture — top right */}
        <button
          className="fixed top-5 right-5 z-50 cursor-pointer rounded-full p-[2px] backdrop-blur-[6px] bg-white/70 shadow-[0_4px_24px_rgba(0,0,0,0.16)]"
          onClick={() => setCurrentPage('user')}
        >
          <img
            src={convexUser?.profilePictureUrl || "/banner-photos/afternoon/001.webp"}
            alt="User"
            className="w-[38px] h-[38px] object-cover object-center rounded-full"
          />
        </button>

        <div className="gap-0 p-0 w-screen">
          <div className="text-black dark:text-white flex-1 h-full overflow-auto min-h-[calc(100vh-48px)] sm:min-h-screen z-0">
            <div style={{ display: currentPage === 'upcoming'   ? 'block' : 'none' }}><UpcomingPage /></div>
            <div style={{ display: currentPage === 'progress'   ? 'block' : 'none' }}><GradesPage /></div>
            <div style={{ display: currentPage === 'activities' ? 'block' : 'none' }}><SchedulePage /></div>
            <div style={{ display: currentPage === 'chat'       ? 'block' : 'none' }}><ChatPage /></div>
            <div style={{ display: currentPage === 'user'       ? 'block' : 'none' }}><UserPage /></div>
          </div>
        </div>
      </div>
    </UserProvider>
  );
}

export function AppLayout() {
  return (
    <LoadingProvider initialStates={{ 'user-fetch': true, 'schoology-refresh': true }}>
      <AppLayoutInner />
      <LoadingScreen />
    </LoadingProvider>
  );
}
