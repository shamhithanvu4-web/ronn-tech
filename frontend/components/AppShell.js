"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { clearSession, getStoredUser, getToken, saveSession } from "@/lib/auth";
import { PRODUCT_NAME } from "@/lib/product";

const UserContext = createContext({ user: null, refreshAlerts: () => {} });

export function useUser() {
  return useContext(UserContext).user;
}

export function useShell() {
  return useContext(UserContext);
}

const LINKS = [
  ["/dashboard", "Dashboard"],
  ["/analyze", "Analyze"],
  ["/cases", "Cases"],
  ["/alerts", "Alerts"],
  ["/entities", "VASP directory"],
  ["/methodology", "Methodology"],
];

export default function AppShell({ children }) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [urgent, setUrgent] = useState(0);

  const refreshAlerts = useCallback(async () => {
    try {
      const r = await api.get("/alerts?urgent=true&unacknowledged=true&limit=1");
      setUrgent(r.unacknowledged_urgent ?? 0);
    } catch {
      /* the alerts badge is best-effort */
    }
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    setUser(getStoredUser());
    setReady(true);
    api
      .get("/auth/me")
      .then((me) => {
        setUser(me);
        saveSession(token, me);
      })
      .catch(() => {
        /* 401 is handled by lib/api.js (redirect to /login) */
      });
    refreshAlerts();
    const t = setInterval(refreshAlerts, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function signOut() {
    clearSession();
    router.replace("/login");
  }

  if (!ready) {
    return <div className="login-shell muted">Checking your session…</div>;
  }

  return (
    <UserContext.Provider value={{ user, refreshAlerts }}>
      <header className="nav">
        <div className="container nav-inner">
          <Link href="/dashboard" className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">{PRODUCT_NAME}</span>
          </Link>
          <nav className="nav-links" aria-label="Main">
            {LINKS.map(([href, label]) => {
              const active = pathname === href || pathname.startsWith(`${href}/`) || (href === "/analyze" && pathname.startsWith("/analyses"));
              return (
                <Link key={href} href={href} className={`nav-link ${active ? "active" : ""}`}>
                  {label}
                  {href === "/alerts" && urgent > 0 ? <span className="nav-badge">{urgent}</span> : null}
                </Link>
              );
            })}
          </nav>
          <div className="nav-user">
            {user ? (
              <>
                <span className="who small">{user.full_name || user.username}</span>
                <span className="role-chip">{user.role}</span>
              </>
            ) : null}
            <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="container page">{children}</main>
    </UserContext.Provider>
  );
}
