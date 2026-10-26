/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.TACTIQ_BACKEND_URL ? "" : process.env.NEXT_PUBLIC_API_BASE_URL || "",
  },
  typedRoutes: true,
  async rewrites() {
    const backend = process.env.TACTIQ_BACKEND_URL;
    if (!backend) return [];
    const target = new URL(backend);
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) {
      throw new Error("TACTIQ_BACKEND_URL must be an HTTP(S) origin without credentials");
    }
    return [
      { source: "/api/:path*", destination: `${target.origin}/api/:path*` },
      { source: "/media/:path*", destination: `${target.origin}/media/:path*` },
    ];
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "same-origin" },
        { key: "X-Frame-Options", value: "DENY" },
      ],
    }];
  },
};

export default nextConfig;
