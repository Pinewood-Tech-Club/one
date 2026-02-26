import type { MobileBridgeContext } from '@/lib/mobileBridge';

declare global {
  interface Window {
    mobileBridge?: {
      v1?: {
        startSchoologyOAuth?: () => void | Promise<void>;
        openExternalURL?: (url: string) => void | Promise<void>;
        onboardingComplete?: () => void | Promise<void>;
        getContext?: () => MobileBridgeContext | Promise<MobileBridgeContext>;
      };
    };
  }
}

export {};
