'use client';

import { createContext, useContext, ReactNode } from 'react';

interface UserContextType {
  userName: string | null;
  userId: string | null;
}

const UserContext = createContext<UserContextType | undefined>(undefined);

export function UserProvider({
  children,
  userName,
  userId,
}: {
  children: ReactNode;
  userName: string | null;
  userId: string | null;
}) {
  return (
    <UserContext.Provider value={{ userName, userId }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  const context = useContext(UserContext);
  if (context === undefined) {
    throw new Error('useUser must be used within a UserProvider');
  }
  return context;
}
