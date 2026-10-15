"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { getToken, saveSession } from "@/lib/auth";
import { PRODUCT_NAME } from "@/lib/product";

function safeNext(next) {
  // Only same-site paths, never an absolute URL.
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/login")) return "/dashboard";
  return next;
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (getToken()) router.replace(next);
  }, [next, router]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(username.trim(), password);
      saveSession(r.access_token, r.user);
      router.replace(next);
    } catch (err) {
      setError(err.detail || err.message);
      setBusy(false);
    }
  }

  return (
    <form className="card card-lg login-card" onSubmit={submit}>
      <div className="stack-sm">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>{PRODUCT_NAME}</span>
        </div>
        <h1>Sign in</h1>
        <p className="muted small">Wallet attribution for authorised law-enforcement investigators. Every action is audit-logged.</p>
      </div>
      <div className="field">
        <label htmlFor="u">Username</label>
        <input id="u" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </div>
      <div className="field">
        <label htmlFor="p">Password</label>
        <input
          id="p"
          className="input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>
      {error ? (
        <div className="form-error" role="alert">
          {error}
        </div>
      ) : null}
      <button className="btn" type="submit" disabled={busy || !username || !password}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="footer-note">Sessions last 60 minutes and end when this tab is closed.</p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="login-shell">
      <Suspense fallback={<div className="card login-card muted">Loading…</div>}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
