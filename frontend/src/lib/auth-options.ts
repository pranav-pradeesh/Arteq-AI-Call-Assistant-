import type { NextAuthOptions } from "next-auth";
import Credentials from "next-auth/providers/credentials";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

/**
 * NextAuth config. The Credentials provider authenticates against the backend
 * RBAC endpoint POST /admin/auth/login (email + password), then reads the role
 * from GET /admin/auth/me. The backend JWT is carried in the session so the
 * browser API client can send it as a Bearer token.
 */
export const authOptions: NextAuthOptions = {
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  providers: [
    Credentials({
      name: "Email + password",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(creds) {
        if (!creds?.email || !creds?.password) return null;
        let res: Response;
        try {
          res = await fetch(`${API_BASE}/admin/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: creds.email, password: creds.password }),
          });
        } catch (e) {
          console.error("[auth] cannot reach backend at", API_BASE, "-", e);
          return null;
        }
        if (!res.ok) return null;
        const { access_token } = (await res.json()) as { access_token: string };

        let role = "viewer";
        let email = creds.email as string;
        try {
          const me = await fetch(`${API_BASE}/admin/auth/me`, {
            headers: { Authorization: `Bearer ${access_token}` },
          });
          if (me.ok) {
            const data = (await me.json()) as { email?: string; role?: string };
            role = data.role ?? role;
            // email stays as the address the user logged in with (token sub is "admin")
          }
        } catch {
          /* keep defaults */
        }
        return { id: email, email, role, accessToken: access_token } as any;
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.accessToken = (user as any).accessToken;
        token.role = (user as any).role;
        token.email = (user as any).email;
      }
      return token;
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string | undefined;
      if (session.user) {
        (session.user as any).role = token.role as string | undefined;
        session.user.email = (token.email as string) ?? session.user.email;
      }
      return session;
    },
  },
};
