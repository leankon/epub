// Request guard for the local proxy. Pure functions so they can be unit tested.
//
// The proxy is what the browser talks to; it adds the backend session token. To stop other
// websites from using it (CSRF, DNS rebinding) it only accepts requests that:
//   * target an allowed Host (127.0.0.1 / localhost on the configured port),
//   * for state-changing methods, come from exactly this origin and carry the X-PDFWS-Client header
//     (which forces a CORS preflight that is never answered for foreign origins),
//   * are never labelled cross-site by the browser's Fetch Metadata.

export interface GuardInput {
  method: string;
  host: string | null;
  origin: string | null;
  secFetchSite: string | null;
  clientHeader: string | null;
}

export interface GuardResult {
  ok: boolean;
  status?: number;
  reason?: string;
}

export function allowedHosts(port: string, extra: string = ""): Set<string> {
  const hosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  for (const h of extra.split(",")) {
    const t = h.trim();
    if (t) hosts.add(t);
  }
  return hosts;
}

const SAFE_METHODS = new Set(["GET", "HEAD"]);

export function checkRequest(req: GuardInput, hosts: Set<string>): GuardResult {
  if (!req.host || !hosts.has(req.host)) {
    return { ok: false, status: 421, reason: "host_not_allowed" };
  }
  const site = req.secFetchSite;
  if (site === "cross-site") {
    return { ok: false, status: 403, reason: "cross_site" };
  }
  const method = req.method.toUpperCase();
  if (SAFE_METHODS.has(method)) {
    // Downloads/images: same-origin page or direct navigation only.
    if (site && site !== "same-origin" && site !== "none") {
      return { ok: false, status: 403, reason: "not_same_origin" };
    }
    if (req.origin && req.origin !== `http://${req.host}`) {
      return { ok: false, status: 403, reason: "origin_mismatch" };
    }
    return { ok: true };
  }
  if (req.origin !== `http://${req.host}`) {
    return { ok: false, status: 403, reason: "origin_mismatch" };
  }
  if (site && site !== "same-origin") {
    return { ok: false, status: 403, reason: "not_same_origin" };
  }
  if (req.clientHeader !== "1") {
    return { ok: false, status: 403, reason: "missing_client_header" };
  }
  return { ok: true };
}

// Only these path shapes are forwarded (no traversal, no absolute URLs).
export function safeBackendPath(segments: string[]): string | null {
  if (segments.length === 0 || segments.length > 12) return null;
  for (const s of segments) {
    if (!/^[A-Za-z0-9._-]+$/.test(s) || s === "." || s === "..") return null;
  }
  return "/api/" + segments.join("/");
}
