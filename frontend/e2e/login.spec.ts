/**
 * login.spec.ts
 *
 * MOCKING STRATEGY
 * ----------------
 * 1. Redirect tests: no mocking needed — middleware (withAuth) runs server-side
 *    and will redirect unauthenticated requests to /login by itself.
 *
 * 2. Wrong-credentials test: we intercept the browser POST that NextAuth's
 *    signIn("credentials", { redirect: false }) fires to
 *    /api/auth/callback/credentials and return a redirect back to
 *    /login?error=CredentialsSignin. NextAuth's client library treats that
 *    redirect as an error and sets res.error, so the login page then renders
 *    "Invalid email or password".
 *
 * KNOWN LIMITATION
 * ----------------
 * A real successful login is NOT tested here because authorize() runs in Node
 * (not the browser) and calls http://localhost:8000 which is absent in CI.
 * That flow is covered by the RBAC session-mock tests instead.
 */

import { test, expect } from "@playwright/test";

// ── Redirect tests ────────────────────────────────────────────────────────────

test("visiting / while unauthenticated redirects to /login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
});

test("visiting /overview while unauthenticated redirects to /login", async ({ page }) => {
  await page.goto("/overview");
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
});

// ── Login form rendering ──────────────────────────────────────────────────────

test("/login renders email field, password field, and Sign in button", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByPlaceholder("you@hospital.com")).toBeVisible();
  await expect(page.getByPlaceholder("••••••••")).toBeVisible();
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
});

test("/login shows brand name 'Arteq Admin'", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByText("Arteq Admin")).toBeVisible();
});

// ── Wrong credentials ─────────────────────────────────────────────────────────

test("submitting wrong credentials shows 'Invalid email or password'", async ({ page }) => {
  await page.goto("/login");

  /**
   * Intercept the NextAuth credentials callback. NextAuth's signIn() with
   * redirect:false follows the Location header returned by the server.
   * Returning a 302 → /login?error=CredentialsSignin mirrors what the real
   * handler returns on bad creds, which makes the client-side signIn() resolve
   * with { ok: false, error: "CredentialsSignin" } and the page shows the
   * error message.
   *
   * Note: if NextAuth's fetch-follow behaviour ever changes, this stub may need
   * to be adjusted. If it becomes brittle, fall back to asserting that the
   * button re-enables after a failed attempt (disabled={loading} in source).
   */
  await page.route("**/api/auth/callback/credentials**", (route) => {
    route.fulfill({
      status: 302,
      headers: {
        Location: "http://localhost:3000/login?error=CredentialsSignin",
      },
      body: "",
    });
  });

  await page.getByPlaceholder("you@hospital.com").fill("bad@example.com");
  await page.getByPlaceholder("••••••••").fill("wrongpassword");
  await page.getByRole("button", { name: /sign in/i }).click();

  await expect(page.getByText(/invalid email or password/i)).toBeVisible({ timeout: 10_000 });
});
