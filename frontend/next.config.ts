import type { NextConfig } from "next";
import { createMDX } from 'fumadocs-mdx/next';

const nextConfig: NextConfig = {
  // Emits .next/standalone with a self-contained server.js and only the
  // production dependencies it actually traced, which is what the Docker
  // runtime stage copies.
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/skibidi/static/:path*",
        destination: "https://us-assets.i.posthog.com/static/:path*",
      },
      {
        source: "/skibidi/:path*",
        destination: "https://us.i.posthog.com/:path*",
      },
    ];
  },
  skipTrailingSlashRedirect: true,
  devIndicators: false,
};

const withMDX = createMDX();

export default withMDX(nextConfig);
