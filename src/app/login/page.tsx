'use client';

import { useEffect } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';

export default function LoginPage() {
  const searchParams = useSearchParams();
  const router = useRouter();

  useEffect(() => {
    const errorParam = searchParams.get('error');

    // If there's an error, redirect to home with params
    if (errorParam) {
      router.push(`/?${searchParams.toString()}`);
      return;
    }

    // Otherwise, redirect to Google OAuth
    window.location.href = `${process.env.NEXT_PUBLIC_BACKEND_URL}/auth/google`;
  }, [searchParams, router]);

  return null;
}

