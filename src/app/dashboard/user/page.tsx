'use client';

import { useEffect, useState } from 'react';

interface User {
  name: string;
  email: string;
  user_id: string;
}

export default function UserPage() {
  const [user, setUser] = useState<User | null>(null);

  // Get user info on mount
  useEffect(() => {
    const fetchUser = async () => {
      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/user`, {
          credentials: 'include',
        });
        if (response.ok) {
          const userData = await response.json();
          setUser(userData);
        }
      } catch (error) {
        console.error('Error fetching user:', error);
      }
    };

    fetchUser();
  }, []);

  return (
    <div className="p-8 space-y-8">
      <h1 className="text-3xl font-bold">User Profile</h1>
      {user ? (
        <div className="border rounded-lg p-6">
          <p className="text-xl">Name: {user.name}</p>
          <p className="text-xl">Email: {user.email}</p>
          <p className="text-xl">User ID: {user.user_id}</p>
        </div>
      ) : (
        <p className="text-gray-500">Loading...</p>
      )}
      <button
        onClick={async () => {
          try {
            const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/auth/logout`, {
              method: 'POST',
              credentials: 'include',
            });

            if (response.ok) {
              window.location.href = '/';
            }
          } catch (error) {
            console.error('Logout error:', error);
          }
        }}
        className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
      >
        Logout
      </button>
    </div>
  );
}

