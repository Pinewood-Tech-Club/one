'use client';

import { useEffect, useState, Component } from 'react';
import type { ReactNode } from 'react';
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
import { useLiveQuery } from '@/hooks/useLiveQuery';
import { getUser, type ApiUser } from '@/lib/api';
import posthog from 'posthog-js';
import { CalendarDays, ChartNoAxesColumnIncreasing, Flag, MessageCircleMore } from 'lucide-react';

class ChatErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div className="flex items-center justify-center min-h-screen text-zinc-400 text-sm">
          Chat is not available for your account.
        </div>
      );
    }
    return this.props.children;
  }
}

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
  const [currentPage, setCurrentPage] = useState<Page>(() => pathnameToPage(pathname));
  const { setLoading } = useLoading();
  const { data: user, isLoading: userLoading } = useLiveQuery<ApiUser>({
    fetcher: getUser,
    events: [{ type: 'user.updated', apply: (d) => (d.user ?? d) as ApiUser }],
  });

  const userName = user?.name ?? 'Loading...';
  const userId = user?.email ?? null;

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
    const normalizedPathname = pathname.length > 1 && pathname.endsWith('/')
      ? pathname.slice(0, -1)
      : pathname;
    if (normalizedPathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  }, [currentPage, pathname]);

  useEffect(() => {
    setLoading('user-fetch', userLoading);
  }, [userLoading, setLoading]);

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
            src={user?.profile_picture_url || "/banner-photos/afternoon/001.webp"}
            alt="User"
            className="w-[38px] h-[38px] object-cover object-center rounded-full"
          />
        </button>

        <div className="gap-0 p-0 w-screen">
          <div className="text-black dark:text-white flex-1 h-full overflow-auto min-h-[calc(100vh-48px)] sm:min-h-screen z-0">
            <div style={{ display: currentPage === 'upcoming'   ? 'block' : 'none' }}><UpcomingPage /></div>
            <div style={{ display: currentPage === 'progress'   ? 'block' : 'none' }}><GradesPage /></div>
            <div style={{ display: currentPage === 'activities' ? 'block' : 'none' }}><SchedulePage /></div>
            <div style={{ display: currentPage === 'chat'       ? 'block' : 'none' }}><ChatErrorBoundary><ChatPage /></ChatErrorBoundary></div>
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
