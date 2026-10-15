"""Shared helpers for bulk label ingestion (plain HTTPS downloads or a local git clone)."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx

from app.util import sha256_hex

UA = "RonnTech-label-ingestion/1.0"


def http_get_text(url: str, timeout: float = 60.0, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA}) as client:
                resp = client.get(url)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.text
        except (httpx.HTTPError,) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"Download failed for {url}: {last}")


def http_get_json(url: str, timeout: float = 60.0):
    import json

    return json.loads(http_get_text(url, timeout=timeout))


def git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def content_hash(*parts: str) -> str:
    return sha256_hex("\n".join(parts))
