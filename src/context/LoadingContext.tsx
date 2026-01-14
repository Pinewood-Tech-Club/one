'use client';

import { createContext, useContext, useState, ReactNode, useCallback } from 'react';

type LoadingStates = Record<string, boolean>;

interface LoadingContextType {
  loadingStates: LoadingStates;
  setLoading: (key: string, isLoading: boolean) => void;
  clearLoading: (key: string) => void;
  isLoading: boolean;
}

const LoadingContext = createContext<LoadingContextType | undefined>(undefined);

export function LoadingProvider({ children }: { children: ReactNode }) {
  const [loadingStates, setLoadingStates] = useState<LoadingStates>({});

  const setLoading = useCallback((key: string, isLoading: boolean) => {
    setLoadingStates((prev) => {
      const updated = { ...prev, [key]: isLoading };
      // Clean up false values to prevent object bloat
      if (!isLoading && key in updated) {
        delete updated[key];
      }
      return updated;
    });
  }, []);

  const clearLoading = useCallback((key: string) => {
    setLoadingStates((prev) => {
      const updated = { ...prev };
      delete updated[key];
      return updated;
    });
  }, []);

  const isLoading = Object.values(loadingStates).some((val) => val === true);

  return (
    <LoadingContext.Provider value={{ loadingStates, setLoading, clearLoading, isLoading }}>
      {children}
    </LoadingContext.Provider>
  );
}

export function useLoading() {
  const context = useContext(LoadingContext);
  if (context === undefined) {
    throw new Error('useLoading must be used within a LoadingProvider');
  }
  return context;
}
