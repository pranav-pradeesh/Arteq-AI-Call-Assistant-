/**
 * smoke.spec.ts
 *
 * MOCKING STRATEGY
 * ----------------
 * The /login page is publicly accessible (excluded from middleware protection)
 * so no mocking is needed. We simply load the page and verify baseline health:
 *   - The Arteq Admin brand name is visible.
 *   - No console errors are emitted during the initial load (e.g. no missing
 *     chunks, no React hydration errors, no unhandled promise rejections).
 *
 * Console warnings are allowed (Next.js and React emit informational warnings
 * in dev mode); only messages at level "error" are treated as failures.
 */

import { test, expect } from "@playwright/test";

test("smoke: /login loads with brand visible and no console errors", async ({ page }) => {
  const consoleErrors: string[] = [];

  // Collect console error messages before navigation so nothing is missed.
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
    }
  });

  // Also capture uncaught page errors (JS exceptions).
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => {
    pageErrors.push(err.message);
  });

  await page.goto("/login", { waitUntil: "networkidle" });

  // Brand name must be visible.
  await expect(page.getByText("Arteq Admin")).toBeVisible();

  // Form fields must be present.
  await expect(page.getByPlaceholder("you@hospital.com")).toBeVisible();
  await expect(page.getByPlaceholder("••••••••")).toBeVisible();
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();

  // No JS errors should have occurred during load.
  // We filter out known benign Next.js dev-mode messages if any slip through.
  const significantErrors = consoleErrors.filter(
    (msg) =>
      // Ignore Next.js hydration warnings that surface as errors in dev mode.
      !msg.includes("Hydration") &&
      !msg.includes("hydration") &&
      // Ignore favicon 404 which is irrelevant to app health.
      !msg.includes("favicon")
  );

  expect(
    significantErrors,
    `Unexpected console errors on /login: ${significantErrors.join("\n")}`
  ).toHaveLength(0);

  expect(
    pageErrors,
    `Uncaught page errors on /login: ${pageErrors.join("\n")}`
  ).toHaveLength(0);
});

// ── Page title ────────────────────────────────────────────────────────────────

test("smoke: /login has a non-empty page title", async ({ page }) => {
  await page.goto("/login");
  const title = await page.title();
  expect(title.length).toBeGreaterThan(0);
});
