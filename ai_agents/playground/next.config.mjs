/** @type {import('next').NextConfig} */
console.log("✅ Loading UPDATED next.config.mjs from  genroot");
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const nextConfig = {
  experimental: {
    // This allows requests from your specific IP address in development mode.
    // In a future version of Next.js, this will be required.
    allowedDevOrigins: [
    "http://47.107.244.67:3000",
    "https://47.107.244.67:3000",
   ],
  },
  // basePath: '/ai-agent',
  // output: 'export',
  output: "standalone",
  reactStrictMode: false,
  // this includes files from the monorepo base two directories up
  outputFileTracingRoot: path.join(__dirname, "./"),
};

export default nextConfig;
