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

export type BridgeInvokeResult =
  | { status: 'invoked' }
  | { status: 'unavailable' }
  | { status: 'error'; error: string };

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
    const result = await this.startSchoologyOAuthDetailed();
    return result.status === 'invoked';
  },

  async startSchoologyOAuthDetailed(): Promise<BridgeInvokeResult> {
    const bridge = getBridge();
    if (!bridge?.startSchoologyOAuth) {
      return { status: 'unavailable' };
    }
    try {
      await bridge.startSchoologyOAuth();
      return { status: 'invoked' };
    } catch (error) {
      return {
        status: 'error',
        error: error instanceof Error ? error.message : 'Bridge call failed',
      };
    }
  },

  async openExternalURL(url: string): Promise<boolean> {
    const result = await this.openExternalURLDetailed(url);
    return result.status === 'invoked';
  },

  async openExternalURLDetailed(url: string): Promise<BridgeInvokeResult> {
    const bridge = getBridge();
    if (!bridge?.openExternalURL) {
      return { status: 'unavailable' };
    }
    try {
      await bridge.openExternalURL(url);
      return { status: 'invoked' };
    } catch (error) {
      return {
        status: 'error',
        error: error instanceof Error ? error.message : 'Bridge call failed',
      };
    }
  },

  async onboardingComplete(): Promise<boolean> {
    const result = await this.onboardingCompleteDetailed();
    return result.status === 'invoked';
  },

  async onboardingCompleteDetailed(): Promise<BridgeInvokeResult> {
    const bridge = getBridge();
    if (!bridge?.onboardingComplete) {
      return { status: 'unavailable' };
    }
    try {
      await bridge.onboardingComplete();
      return { status: 'invoked' };
    } catch (error) {
      return {
        status: 'error',
        error: error instanceof Error ? error.message : 'Bridge call failed',
      };
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
