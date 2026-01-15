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

  // Sync pathname to currentPage state (for initial load and back/forward navigation)
  useEffect(() => {
    const page = pathnameToPage(pathname);
    setCurrentPage(page);
  }, [pathname]);

  // Update URL when currentPage changes (without triggering navigation)
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

  const navItems: Array<{ name: string; icon: string; page: Page }> = [
    { name: 'Upcoming', icon: 'upcoming', page: 'upcoming' }, // icon: @/public/icons/upcoming.svg
    { name: 'Schedule', icon: 'schedule', page: 'schedule' },
    { name: 'Grades', icon: 'grades', page: 'grades' },
    { name: 'Chat', icon: 'chat', page: 'chat' },
  ];


  return (
    <UserProvider userName={userName} userId={userId}>
      <div>
        <div className="bg-green-800 flex flex-col gap-0 p-0 sm:p-[8px] w-screen h-screen">
        {/* Sidebar */}
        <div
          className={`hidden sm:flex flex-row justify-between gap-0 p-0 pb-2 h-auto w-full px-[.375rem]`}
        >
          <div className="flex flex-row gap-[16px] flex-1 flex-none">
            {/* Navigation items */}
            {navItems.map((item) => {
              const isActive = currentPage === item.page;
              return (
                <button
                  key={item.page}
                  onClick={() => setCurrentPage(item.page)}
                  className={`flex items-center justify-center flex-1 p-0 text-white text-[18px] cursor-pointer transition-colors`}
                >
                  <div className="flex flex-col justify-center h-[20px] w-[20px] text-center shrink-0 mr-[8px]">
                    <img src={`/icons/${item.icon}${isActive ? '.filled' : ''}.svg`} alt={item.name} className="h-[30px] w-auto" />
                  </div>
                  <div className={`flex-col justify-center font-['Inter'] ${isActive ? 'font-bold' : 'font-normal'}`}>
                    <p>{item.name}</p>
                  </div>
                </button>
              );
            })}
          </div>
          {/* Bottom user profile */}
          <div>
            <button
              onClick={() => setCurrentPage('user')}
              className={`flex items-center justify-center p-0 text-white text-[24px] cursor-pointer transition-colors`}
            >
              <div className="flex-col justify-center font-['Inter'] font-normal text-[18px] overflow-hidden">
                <p className="leading-normal truncate">{userName}</p>
              </div>
            </button>
          </div>
        </div>
        {/* Main content area */}
        <div className="bg-white sm:rounded-md flex-1 h-full overflow-auto">
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
      
        {/* Mobile menu */}
        <div className="flex sm:hidden flex-row justify-between gap-0 p-0 h-auto w-full px-[.375rem]">
          <div className="flex flex-row gap-[16px] flex-1 flex-none">
            {navItems.map((item) => {
              const isActive = currentPage === item.page;
              return (
                <button
                  key={item.page}
                  onClick={() => setCurrentPage(item.page)}
                  className={`flex items-center justify-center flex-1 p-0 text-white text-[18px] cursor-pointer transition-colors`}
                >
                  <div className="flex flex-col justify-center h-[20px] w-[20px] text-center mr-[8px]">
                    <img src={`/icons/${item.icon}.svg`} alt={item.name} className="h-[30px] w-auto" />
                  </div>
                </button>
                );
            })}
          </div>
        </div>
      </div>
      </div>
    </UserProvider>
  );
}

export function AppLayout() {
  return (
    <LoadingProvider>
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
