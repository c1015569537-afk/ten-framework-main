console.log("✅  Loading UPDATED next.config.mjs from speechmatics-diarization !");
/** @type {import('next').NextConfig} */
const nextConfig = {
  // This allows requests from your specific IP address in development mode.
  // In a future version of Next.js, this will be required.
  allowedDevOrigins: [
    "http://47.107.244.67",
    "https://47.107.244.67",
    "47.107.244.67",
    "ws:47.107.244.67",
    "wss:47.107.244.67",
    "socket3.speediance.top",
    "socket3.speediance.top/api/ten",
    'localhost',
    '127.0.0.1'
  ],
  output: 'standalone',
  reactStrictMode: false,
}

export default nextConfig


