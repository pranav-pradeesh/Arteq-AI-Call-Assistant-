import { withAuth } from "next-auth/middleware";
import { NextResponse } from "next/server";

// Routes only super_admin may open.
const SUPER_ADMIN_ONLY = ["/users", "/tenants"];

export default withAuth(
  function middleware(req) {
    const { pathname } = req.nextUrl;
    const role = req.nextauth.token?.role;
    if (SUPER_ADMIN_ONLY.some((p) => pathname.startsWith(p)) && role !== "super_admin") {
      return NextResponse.redirect(new URL("/overview", req.url));
    }
    return NextResponse.next();
  },
  {
    callbacks: {
      // Authenticated iff a NextAuth token exists.
      authorized: ({ token }) => !!token,
    },
    pages: { signIn: "/login" },
  }
);

// Protect everything except the login page, the public /mock preview, the auth
// API, Next internals, and
// any static asset (paths containing a dot, e.g. logo.svg, favicon.ico). Without
// excluding static files, the auth middleware would redirect /logo.svg to /login
// and the logo would fail to load on the logged-out login page.
export const config = {
  matcher: ["/((?!login|mock|api/auth|_next/static|_next/image|.*\\..*).*)"],
};
