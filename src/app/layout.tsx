import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ConvexClientProvider } from "@/components/ConvexClientProvider";
import { LayoutWrapper } from "@/components/LayoutWrapper";
import { LoadingProvider } from "@/context/LoadingContext";
import { LoadingScreen } from "@/components/LoadingScreen";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Pinewood One",
  description: "skibidi",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${inter.variable} antialiased`}
      >
        <ConvexClientProvider>
          <LoadingProvider>
            <LayoutWrapper>
              {children}
            </LayoutWrapper>
            <LoadingScreen />
          </LoadingProvider>
        </ConvexClientProvider>
      </body>
    </html>
  );
}
