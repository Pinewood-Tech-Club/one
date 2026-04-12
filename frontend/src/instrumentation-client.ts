import posthog from 'posthog-js';

const posthogKey = process.env.NEXT_PUBLIC_POSTHOG_KEY;

if (posthogKey) {
  posthog.init(posthogKey, {
    api_host: '/skibidi',
    ui_host: '/skibidi',

    // Keep analytics anonymous - no user identification
    person_profiles: 'identified_only',

    // Session replay with privacy masking
    session_recording: {
      maskAllInputs: true,
      maskTextSelector: '[data-ph-mask]',
    },

    // Error tracking - automatic capture
    capture_exceptions: {
      capture_unhandled_errors: true,
      capture_unhandled_rejections: true,
      capture_console_errors: false,
    },

    autocapture: true,
    capture_pageview: true,
    capture_pageleave: true,
  });
}

export { posthog };
