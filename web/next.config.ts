import type { NextConfig } from "next";

// The FastAPI service. Set API_URL in the environment for a deployment; the default
// is the local dev server started with `make run`.
const API_URL = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone", // self-contained server for the Docker image
  agentRules: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
