import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// next-auth/react is used by the API client and shell; stub it for unit tests.
vi.mock("next-auth/react", () => ({
  getSession: vi.fn(async () => null),
  useSession: () => ({ data: null, status: "unauthenticated" }),
  signIn: vi.fn(),
  signOut: vi.fn(),
  SessionProvider: ({ children }: { children: unknown }) => children,
}));

afterEach(() => cleanup());
