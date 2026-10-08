import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const PASS_REQUEST = ["content-type", "accept"];
const PASS_RESPONSE = ["content-type", "content-disposition", "cache-control"];

/** Authenticated proxy: forwards /api/backend/* to the FastAPI backend with the user's API token. */
async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const token = await getToken({
    req,
    secret: process.env.AUTH_SECRET,
    secureCookie: (process.env.AUTH_URL ?? "").startsWith("https://"),
  });
  if (!token?.backendToken) {
    return NextResponse.json({ detail: "Not signed in" }, { status: 401 });
  }

  const { path } = await ctx.params;
  const url = `${BACKEND_URL}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers({ authorization: `Bearer ${token.backendToken}` });
  for (const h of PASS_REQUEST) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const res = await fetch(url, {
    method: req.method,
    headers,
    body: hasBody ? await req.arrayBuffer() : undefined,
    cache: "no-store",
  });

  const out = new Headers();
  for (const h of PASS_RESPONSE) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(res.body, { status: res.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
