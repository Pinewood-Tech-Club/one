"use client";

import { redirect } from 'next/navigation';

function safeBase64Decode(value: string): string | null {
  try {
    // Strict Base64 check (allows padding)
    const base64Regex =
      /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

    if (!base64Regex.test(value)) {
      return null;
    }

    return atob(value);
  } catch {
    return null;
  }
}

export function Home() {
  const searchParams = new URLSearchParams(window.location.search);
  const errorParam = searchParams.get("error");
  const errorMessage =
  errorParam !== null
    ? safeBase64Decode(errorParam) ?? "An error occurred."
    : null;
  return (
    <div>
        <div className="w-screen h-screen bg-green-800 text-white">
            <div className="flex flex-col items-center justify-center h-full gap-4">
                <p className="text-5xl font-bold text-center hover:scale-105 transition-transform ease-in-out cursor-default">COMING SOON</p>
                <p>Pinewood One yada yada pls email for more info and stuff</p>
                <p>from the pinewood tech club</p>
                <p><a href="mailto:techclub@pinewood.edu" className="block text-white underline px-3 py-2 rounded-lg hover:text-blue-100 hover:bg-green-700 hover:scale-105 transition-transform ease-in-out cursor-pointer" style={{
                    transition: "color 0.2s ease, background-color 0.2s ease, scale 0.2s ease",
                }}>techclub@pinewood.edu</a></p>
                {/* if there is an error=parameter decode the base64 and display it */}
                {errorMessage && (
                    <p>
                      {errorMessage}
                    </p>
                )}
            </div>
        </div>
    </div>
  );
}

export default function HomePage() {
  redirect('/');
}

