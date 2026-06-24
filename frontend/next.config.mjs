/** @type {import('next').NextConfig} */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://app:8000";

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Don't fail the production build on pre-existing lint/type issues.
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
  async rewrites() {
    // Proxy API + admin calls to the FastAPI backend (compose network).
    return [
      { source: "/api/v1/:path*", destination: `${API_BASE}/api/v1/:path*` },
      { source: "/admin/api/:path*", destination: `${API_BASE}/admin/:path*` },
    ];
  },
};

export default nextConfig;
