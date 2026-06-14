/** @type {import('next').NextConfig} */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy API + admin calls to the FastAPI backend during dev so the
    // browser talks to one origin (avoids CORS while iterating).
    return [
      { source: "/api/v1/:path*", destination: `${API_BASE}/api/v1/:path*` },
      { source: "/admin/api/:path*", destination: `${API_BASE}/admin/:path*` },
    ];
  },
};

export default nextConfig;
