// Browser API client. Every request goes to this app's own proxy (/api/backend/...).
import type { DocumentSummary, Job } from "./types";

export const API = "/api/backend";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
    const e = body.error ?? {};
    return new ApiError(res.status, e.code ?? "error", e.message ?? `Request failed (${res.status})`, e.details ?? {});
  } catch {
    return new ApiError(res.status, "error", `Request failed (${res.status})`);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("x-pdfws-client", "1");
  if (init.body && typeof init.body === "string" && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(API + path, { ...init, headers, cache: "no-store" });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

export function uploadFile(file: File, onProgress?: (fraction: number) => void): Promise<DocumentSummary> {
  // XMLHttpRequest gives real upload progress; the body is streamed by the proxy.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/documents?filename=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("x-pdfws-client", "1");
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as DocumentSummary);
      else {
        const e = (body as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
        reject(new ApiError(xhr.status, e?.code ?? "upload_failed", e?.message ?? "Upload failed", e?.details ?? {}));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Could not reach the local application."));
    xhr.send(file);
  });
}

export interface SubmitOptions {
  acknowledgeWarnings?: boolean;
  idempotencyKey?: string;
}

export function submitJob(kind: string, versionIds: string[], params: Record<string, unknown>, opts: SubmitOptions = {}) {
  return post<Job>("/jobs", {
    kind,
    version_ids: versionIds,
    params,
    acknowledge_warnings: opts.acknowledgeWarnings ?? false,
    idempotency_key: opts.idempotencyKey,
  });
}

export const downloadUrl = (versionId: string) => `${API}/versions/${versionId}/download`;
export const thumbUrl = (versionId: string, page: number, w = 240) =>
  `${API}/versions/${versionId}/pages/${page}/thumbnail?w=${w}`;
