import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server.js + traced deps, for the Docker image
  // (infra/docker/frontend.Dockerfile). `next dev`/`next start` unaffected.
  output: "standalone",
};

export default nextConfig;
