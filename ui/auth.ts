import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

/**
 * Google SSO. On sign-in, the Google ID token is exchanged for an API token at POST /auth/google;
 * the backend verifies the Google token itself. The API token lives only in the encrypted
 * session cookie and is attached server-side by the /api/backend proxy route.
 */
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  ],
  session: { strategy: "jwt" },
  pages: { signIn: "/login", error: "/login" },
  trustHost: true,
  callbacks: {
    async jwt({ token, account }) {
      if (account?.provider === "google") {
        if (!account.id_token) throw new Error("Google did not return an ID token");
        const res = await fetch(`${BACKEND_URL}/auth/google`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ id_token: account.id_token }),
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`Backend rejected sign-in (${res.status})`);
        const data = (await res.json()) as { access_token: string; expires_at: number };
        token.backendToken = data.access_token;
        token.backendTokenExpires = data.expires_at;
      }
      return token;
    },
    async session({ session, token }) {
      // Expire the UI session together with the API token
      if (token.backendTokenExpires && Date.now() / 1000 > token.backendTokenExpires) {
        session.expired = true;
      }
      return session;
    },
  },
});
