import { describe, expect, it } from "vitest";
import { allowedHosts, checkRequest, safeBackendPath } from "@/lib/guard";

const hosts = allowedHosts("3000");
const base = { host: "127.0.0.1:3000", origin: "http://127.0.0.1:3000", secFetchSite: "same-origin", clientHeader: "1" };

describe("proxy guard", () => {
  it("accepts same-origin POST with the client header", () => {
    expect(checkRequest({ ...base, method: "POST" }, hosts).ok).toBe(true);
  });
  it("rejects foreign Host headers (DNS rebinding)", () => {
    expect(checkRequest({ ...base, method: "GET", host: "evil.example:3000" }, hosts)).toMatchObject({ ok: false, status: 421 });
  });
  it("rejects cross-site requests", () => {
    expect(checkRequest({ ...base, method: "POST", origin: "https://evil.example", secFetchSite: "cross-site" }, hosts).ok).toBe(false);
    expect(checkRequest({ ...base, method: "GET", secFetchSite: "cross-site" }, hosts).ok).toBe(false);
  });
  it("rejects same-site but different-port origins", () => {
    expect(checkRequest({ ...base, method: "POST", origin: "http://localhost:5173", secFetchSite: "same-site" }, hosts).ok).toBe(false);
    expect(checkRequest({ ...base, method: "GET", secFetchSite: "same-site", origin: null }, hosts).ok).toBe(false);
  });
  it("requires the client header for state-changing requests", () => {
    expect(checkRequest({ ...base, method: "DELETE", clientHeader: null }, hosts)).toMatchObject({ ok: false, reason: "missing_client_header" });
  });
  it("allows direct navigation downloads", () => {
    expect(checkRequest({ ...base, method: "GET", origin: null, secFetchSite: "none", clientHeader: null }, hosts).ok).toBe(true);
  });
  it("only forwards safe paths", () => {
    expect(safeBackendPath(["documents", "abc"])).toBe("/api/documents/abc");
    expect(safeBackendPath([".."])).toBeNull();
    expect(safeBackendPath(["a/b"])).toBeNull();
    expect(safeBackendPath(["%2e%2e"])).toBeNull();
    expect(safeBackendPath([])).toBeNull();
  });
});
