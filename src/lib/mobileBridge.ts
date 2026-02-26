export type MobileBridgeContext = {
  platform?: string;
  appVersion?: string;
  buildNumber?: string;
  bridgeVersion?: string;
  [key: string]: unknown;
};

type MobileBridgeV1 = {
  startSchoologyOAuth: () => void | Promise<void>;
  openExternalURL: (url: string) => void | Promise<void>;
  onboardingComplete: () => void | Promise<void>;
  getContext: () => MobileBridgeContext | Promise<MobileBridgeContext>;
};

type PartialMobileBridgeV1 = Partial<MobileBridgeV1>;

function getBridge(): PartialMobileBridgeV1 | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return window.mobileBridge?.v1 ?? null;
}

export const mobileBridge = {
  isAvailable(): boolean {
    return getBridge() !== null;
  },

  async startSchoologyOAuth(): Promise<boolean> {
    const bridge = getBridge();
    if (!bridge?.startSchoologyOAuth) {
      return false;
    }
    try {
      await bridge.startSchoologyOAuth();
      return true;
    } catch {
      return false;
    }
  },

  async openExternalURL(url: string): Promise<boolean> {
    const bridge = getBridge();
    if (!bridge?.openExternalURL) {
      return false;
    }
    try {
      await bridge.openExternalURL(url);
      return true;
    } catch {
      return false;
    }
  },

  async onboardingComplete(): Promise<boolean> {
    const bridge = getBridge();
    if (!bridge?.onboardingComplete) {
      return false;
    }
    try {
      await bridge.onboardingComplete();
      return true;
    } catch {
      return false;
    }
  },

  async getContext(): Promise<MobileBridgeContext | null> {
    const bridge = getBridge();
    if (!bridge?.getContext) {
      return null;
    }
    try {
      return await bridge.getContext();
    } catch {
      return null;
    }
  },
};
