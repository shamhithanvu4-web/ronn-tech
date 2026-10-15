// Session storage for the JWT (SPEC §8: kept in sessionStorage, sent as a Bearer header).
const TOKEN_KEY = "rt.token";
const USER_KEY = "rt.user";

function store() {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function getToken() {
  const s = store();
  return s ? s.getItem(TOKEN_KEY) : null;
}

export function getStoredUser() {
  const s = store();
  if (!s) return null;
  try {
    return JSON.parse(s.getItem(USER_KEY) || "null");
  } catch {
    return null;
  }
}

export function saveSession(token, user) {
  const s = store();
  if (!s) return;
  s.setItem(TOKEN_KEY, token);
  s.setItem(USER_KEY, JSON.stringify(user || null));
}

export function clearSession() {
  const s = store();
  if (!s) return;
  s.removeItem(TOKEN_KEY);
  s.removeItem(USER_KEY);
}

export function hasRole(user, ...roles) {
  return Boolean(user && roles.includes(user.role));
}
