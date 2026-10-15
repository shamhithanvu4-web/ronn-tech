// Every API call goes through this module (SPEC §12).
import { clearSession, getToken } from "./auth";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(status, code, detail) {
    super(detail || code || `HTTP ${status}`);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function redirectToLogin() {
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

async function request(path, { method = "GET", body, form, raw = false, auth = true } = {}) {
  const headers = {};
  const token = getToken();
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) {
    payload = form;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`${API_URL}/api${path}`, { method, headers, body: payload, cache: "no-store" });
  } catch {
    throw new ApiError(0, "network_error", `Cannot reach the API at ${API_URL}. Check that the backend is running.`);
  }
  if (res.status === 401 && auth) {
    clearSession();
    redirectToLogin();
    let data = {};
    try {
      data = await res.json();
    } catch {
      /* empty body */
    }
    throw new ApiError(401, data.code || "unauthorized", data.detail || "Please sign in.");
  }
  if (!res.ok) {
    let data = {};
    try {
      data = await res.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, data.code || "http_error", data.detail || res.statusText || `HTTP ${res.status}`);
  }
  if (raw) return res;
  if (res.status === 204) return null;
  return res.json();
}

function filenameFrom(res, fallback) {
  const cd = res.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename="?([^";]+)"?/i);
  return m ? m[1] : fallback;
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body: body ?? {} }),
  patch: (path, body) => request(path, { method: "PATCH", body: body ?? {} }),
  del: (path) => request(path, { method: "DELETE" }),
  upload: (path, file) => {
    const form = new FormData();
    form.append("file", file);
    return request(path, { method: "POST", form });
  },
  form: (path, form) => request(path, { method: "POST", form }),
  login: (username, password) => request("/auth/login", { method: "POST", body: { username, password }, auth: false }),
  // Authenticated binary download (PDF/JSON reports). Returns a Blob plus the report hash.
  file: async (path, fallbackName) => {
    const res = await request(path, { raw: true });
    const blob = await res.blob();
    return { blob, sha256: res.headers.get("X-Report-SHA256"), filename: filenameFrom(res, fallbackName) };
  },
};

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
