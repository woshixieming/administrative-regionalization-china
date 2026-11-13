import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  ...(process.env.NODE_ENV === "development" ? {
    async rewrites() {
      const origin = process.env.DIVISIONS_API_ORIGIN ?? "http://127.0.0.1:3100";
      return [{ source: "/api/:path*", destination: `${origin}/api/:path*` }];
    },
  } : { output: "export" as const }),
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
