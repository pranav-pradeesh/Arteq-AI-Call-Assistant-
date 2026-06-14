import { defineConfig, devices } from "@playwright/test";

/**
 * E2E config. Tests stub the backend via page.route (see e2e/*.spec.ts), so a
 * real FastAPI backend is NOT required — only the Next dev server, which
 * Playwright starts automatically below.
 *
 * NEXTAUTH_SECRET is needed for the NextAuth route; a dummy value is fine for
 * tests since auth is mocked at the network layer.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000/login",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      NEXTAUTH_SECRET: "test-secret-not-for-production",
      NEXTAUTH_URL: "http://localhost:3000",
      NEXT_PUBLIC_API_BASE: "http://localhost:8000",
    },
  },
});
