import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Note: output: "standalone" is enabled for production Docker/Node deployments.
  // Disable it locally if you just want `next dev` / `next start` to work without
  // copying the .next/standalone folder around.
  output: "standalone",
  // FE-011/FE-012/FE-013 ROOT FIX: typescript.ignoreBuildErrors was previously
  // `true`, which let broken imports (sidebarCategories, ScreenCategory,
  // getScreenMeta, dashboardStats, Pill, etc.) silently pass the build. At
  // runtime the components crashed because the imports resolved to undefined.
  //
  // Production-grade code MUST fail the build on type errors. If there are
  // legitimate `@ts-expect-error` annotations, they are still respected —
  // `ignoreBuildErrors: false` only fails the build on UNEXPECTED type errors.
  typescript: {
    ignoreBuildErrors: false,
  },
  // FE-028 ROOT FIX: reactStrictMode was disabled — React 19's built-in
  // bug detection was off. Strict mode in development double-renders
  // components, double-invokes effects, and warns about deprecated APIs.
  // This catches stale closures, missing effect cleanups, and deprecated
  // patterns BEFORE they reach production. Disabling it in a production
  // pharma app is a code smell.
  reactStrictMode: true,
};

export default nextConfig;
