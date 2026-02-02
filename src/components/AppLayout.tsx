'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { LoadingProvider, useLoading } from '@/context/LoadingContext';
import { UserProvider } from '@/context/UserContext';
import UpcomingPage from '@/app/dashboard/upcoming/page';
import SchedulePage from '@/app/dashboard/schedule/page';
import GradesPage from '@/app/dashboard/grades/page';
import ChatPage from '@/app/dashboard/chat/page';
import UserPage from '@/app/dashboard/user/page';
import { LoadingScreen } from '@/components/LoadingScreen';
// import { useNavbarContrast } from '@/hooks/useNavbarContrast';
import { useTheme } from 'next-themes';
import { useQuery } from 'convex/react';
import { api } from '../../convex/_generated/api';
import posthog from 'posthog-js';

type Page = 'upcoming' | 'schedule' | 'grades' | 'chat' | 'user';

// Map pathname to page
function pathnameToPage(pathname: string): Page {
  if (pathname === '/' || pathname === '/upcoming') return 'upcoming';
  if (pathname === '/schedule') return 'schedule';
  if (pathname === '/grades') return 'grades';
  if (pathname === '/chat') return 'chat';
  if (pathname === '/user') return 'user';
  return 'upcoming'; // default
}

// Map page to pathname
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
  // const isOverDark = useNavbarContrast({ trigger: currentPage });
  // for performance reasons, just check if dark mode is enabled
  // const isOverDark = useTheme().theme === 'dark';

  // Sliding indicator state and refs
  const navContainerRef = useRef<HTMLDivElement>(null);
  const navItemRefs = useRef<Map<Page, HTMLDivElement | null>>(new Map());
  const [indicatorStyle, setIndicatorStyle] = useState({ left: 0, width: 0 });
  const [hoveredPage, setHoveredPage] = useState<Page | null>(null);

  // Update indicator position based on target page
  const updateIndicatorPosition = useCallback((targetPage: Page) => {
    const container = navContainerRef.current;
    const target = navItemRefs.current.get(targetPage);
    if (!container || !target) return;

    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();

    setIndicatorStyle({
      left: targetRect.left - containerRect.left,
      width: targetRect.width,
    });
  }, []);

  // Sync pathname to currentPage state (for initial load and back/forward navigation)
  useEffect(() => {
    const page = pathnameToPage(pathname);
    setCurrentPage(page);
  }, [pathname]);

  // Track tab switches with PostHog
  useEffect(() => {
    posthog.capture('tab_switched', { tab: currentPage });
  }, [currentPage]);

  // Update URL when currentPage changes (without triggering navigation)
  useEffect(() => {
    updateIndicatorPosition(currentPage);
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

  // Auto-refresh Schoology data on page load
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
          // Not connected to Schoology or other error - silently ignore
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

  // Update indicator position when hover or active page changes
  // useEffect(() => {
  //   const targetPage = hoveredPage ?? currentPage;
  //   updateIndicatorPosition(targetPage);
  // }, [hoveredPage, currentPage, updateIndicatorPosition]);

  // Handle window resize - recalculate position
  useEffect(() => {
    const handleResize = () => updateIndicatorPosition(hoveredPage ?? currentPage);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [hoveredPage, currentPage, updateIndicatorPosition]);

  // Handle window blur - reset hover when user switches windows
  // useEffect(() => {
  //   const handleBlur = () => setHoveredPage(null);
  //   window.addEventListener('blur', handleBlur);
  //   return () => window.removeEventListener('blur', handleBlur);
  // }, []);

  const navItems: Array<{ name: string; icon: string; page: Page }> = [
    { name: 'Upcoming', icon: 'upcoming', page: 'upcoming' }, // icon: @/public/icons/upcoming.svg
    { name: 'Schedule', icon: 'schedule', page: 'schedule' },
    { name: 'Grades', icon: 'grades', page: 'grades' },
    { name: 'Chat', icon: 'chat', page: 'chat' },
  ];


  return (
    <UserProvider userName={userName} userId={userId}>
      <div>
        <div
          data-navbar
          // className="fixed w-full md:w-2/3 p-2 sm:top-4 left-1/2 -translate-x-1/2 max-w-[640px] sm:rounded-xl backdrop-blur-sm bg-white/10 dark:bg-gray-700/30 shadow-[0_4px_12px_rgba(0,0,0,0.1)] z-50"
          className="fixed w-auto p-1 top-4 left-1/2 -translate-x-1/2 max-w-[640px] rounded-full bg-green-800 z-50"
        >
          <div
            ref={navContainerRef}
            className="flex flex-row justify-between relative"
            onMouseLeave={() => setHoveredPage(null)}
          >
            {/* Sliding indicator */}
            <div
              className={`absolute bg-green-700 rounded-full pointer-events-none`}
              style={{
                left: indicatorStyle.left,
                width: indicatorStyle.width,
                height: '100%',
                top: 0,
                transition: 'left 250ms ease-out, width 250ms ease-out, transform 150ms ease-out',
              }}
            />
            <div className="flex flex-row gap-2 flex-1 flex-none">
              {/* Navigation items */}
              {navItems.map((item) => {
                return (
                  <button
                    key={item.page}
                    onClick={() => setCurrentPage(item.page)}
                    onMouseEnter={() => setHoveredPage(item.page)}
                    className="flex items-center justify-center flex-1 p-0 text-[18px] cursor-pointer text-white relative z-10 hover:bg-green-700/50 rounded-full"
                    style={{
                      transition: 'background-color 150ms ease-out',
                    }}
                    data-nav-item={item.page}
                  >
                    <div
                      ref={(el) => {
                        navItemRefs.current.set(item.page, el);
                      }}
                      className="flex-col justify-center font-['Inter'] p-1"
                    >
                      <p>{item.name}</p>
                    </div>
                  </button>
                );
              })}
            </div>
            {/* User button */}
            <button
              key="user"
              onClick={() => setCurrentPage('user')}
              onMouseEnter={() => setHoveredPage('user')}
              className="flex items-center justify-center flex-1 p-0 text-[18px] cursor-pointer text-white relative z-10 hover:bg-green-700/50 rounded-full ml-8"
              style={{
                transition: 'background-color 150ms ease-out',
              }}
              data-nav-item="user"
            >
              <div
                ref={(el) => {
                  navItemRefs.current.set('user', el);
                }}
                className="flex-col justify-center font-['Inter'] p-1"
              >
                <img src={convexUser?.profilePictureUrl || "/banner-photos/afternoon/001.webp"} alt="User" className="w-8 h-8 object-cover object-center rounded-full" />
              </div>
            </button>
          </div>
        </div>
        <div className="gap-0 p-0 w-screen">
          {/* Main content area */}
          <div className="text-black dark:text-white flex-1 h-full overflow-auto min-h-[calc(100vh-48px)] sm:min-h-screen z-0">
            <div style={{ display: currentPage === 'upcoming' ? 'block' : 'none' }}>
              <UpcomingContent />
            </div>
            <div style={{ display: currentPage === 'schedule' ? 'block' : 'none' }}>
              <ScheduleContent />
            </div>
            <div style={{ display: currentPage === 'grades' ? 'block' : 'none' }}>
              <GradesContent />
            </div>
            <div style={{ display: currentPage === 'chat' ? 'block' : 'none' }}>
              <ChatContent />
            </div>
            <div style={{ display: currentPage === 'user' ? 'block' : 'none' }}>
              <UserContent />
            </div>
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

// Page content components - using actual page components
const UpcomingContent = UpcomingPage;
const ScheduleContent = SchedulePage;
const GradesContent = GradesPage;
const ChatContent = ChatPage;
const UserContent = UserPage;
