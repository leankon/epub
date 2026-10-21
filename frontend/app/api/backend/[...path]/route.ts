// Local proxy to the FastAPI backend. Adds the session token server-side so browser code never
// sees it, and refuses requests that do not come from this application's own pages.
import { readFileSync, statSync } from "fs";
import path from "path";
import { NextRequest } from "next/server";
import { allowedHosts, checkRequest, safeBackendPath } from "@/lib/guard";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND = `http://127.0.0.1:${process.env.PDFWS_BACKEND_PORT ?? "8765"}`;
const TOKEN_FILE =
  process.env.PDFWS_SESSION_TOKEN_FILE ?? path.resolve(process.cwd(), "..", "data", "run", "session-token");
const HOSTS = allowedHosts(process.env.PDFWS_FRONTEND_PORT ?? "3000", process.env.PDFWS_EXTRA_ALLOWED_HOSTS ?? "");
const PASS_REQUEST_HEADERS = ["content-type", "accept", "range"];
const PASS_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "content-length",
  "x-content-sha256",
  "cache-control",
  "content-security-policy",
  "x-frame-options",
];

let cached: { token: string; mtime: number } | null = null;

function token(): string {
  const st = statSync(TOKEN_FILE);
  if (!cached || cached.mtime !== st.mtimeMs) {
    cached = { token: readFileSync(TOKEN_FILE, "utf8").trim(), mtime: st.mtimeMs };
  }
  return cached.token;
}

function deny(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message, details: {} } }, { status });
}

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const verdict = checkRequest(
    {
      method: req.method,
      host: req.headers.get("host"),
      origin: req.headers.get("origin"),
      secFetchSite: req.headers.get("sec-fetch-site"),
      clientHeader: req.headers.get("x-pdfws-client"),
    },
    HOSTS,
  );
  if (!verdict.ok) return deny(verdict.status ?? 403, verdict.reason ?? "forbidden", "Request refused by local proxy.");
  const { path: segments } = await ctx.params;
  const target = safeBackendPath(segments);
  if (!target) return deny(400, "bad_path", "Invalid API path.");

  let tok: string;
  try {
    tok = token();
  } catch {
    return deny(503, "backend_not_ready", "Backend session token not found. Is the backend running?");
  }
  const headers = new Headers({ "x-pdfws-token": tok });
  for (const h of PASS_REQUEST_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(BACKEND + target + req.nextUrl.search, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // Stream request bodies (large uploads) instead of buffering them.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    } as RequestInit);
  } catch {
    return deny(502, "backend_unreachable", "The local backend is not reachable. Start it with ./scripts/dev.sh.");
  }
  const out = new Headers();
  for (const h of PASS_RESPONSE_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  out.set("x-content-type-options", "nosniff");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
