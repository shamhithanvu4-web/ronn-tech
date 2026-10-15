"""Ransomwhere ransomware payment addresses (tier B, RANSOMWARE).

Source: ransomwhere.json from the latest Zenodo record linked at https://ransomwhe.re
(open dataset by Cable, Ransomwhere project). Use --file to ingest a manual download.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.db import iso, utcnow
from app.engine.validate import validate_address
from app.errors import InvalidAddress
from app.labels.sources.common import content_hash, http_get_json, http_get_text
from app.labels.store import IngestResult, LabelRow

HOME = "https://ransomwhe.re"
ZENODO_API = "https://zenodo.org/api/records/{id}"
FILE_NAME = "ransomwhere.json"
RECORD_RE = re.compile(r"(?:zenodo\.org/(?:records?|record)/(\d+))|(?:10\.5281/zenodo\.(\d+))")

CHAINS = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "tron": "tron"}


def find_record_id(html: str) -> str | None:
    m = RECORD_RE.search(html)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _file_url(record: dict[str, Any]) -> str | None:
    files = record.get("files") or []
    if isinstance(files, dict):
        files = files.get("entries") or list(files.values())
    for f in files:
        key = f.get("key") or f.get("filename")
        if key == FILE_NAME:
            links = f.get("links") or {}
            return links.get("content") or links.get("self") or links.get("download")
    return None


def parse(data: Any, source_url: str, retrieved: str) -> tuple[list[LabelRow], dict[str, int]]:
    items = data.get("result") if isinstance(data, dict) else data
    rows: list[LabelRow] = []
    stats = {"items": 0, "rejected_invalid": 0, "unsupported_chain": 0}
    for item in items or []:
        stats["items"] += 1
        addr = str(item.get("address") or "").strip()
        chain = CHAINS.get(str(item.get("blockchain") or "bitcoin").lower())
        if not addr or chain is None:
            stats["unsupported_chain"] += 1
            continue
        try:
            addr = validate_address(addr).address
        except InvalidAddress:
            stats["rejected_invalid"] += 1
            continue
        family = str(item.get("family") or "Unknown ransomware").strip()
        rows.append(
            LabelRow(
                chain=chain,
                address=addr,
                entity_name=f"{family} (ransomware)",
                entity_type="RANSOMWARE",
                is_vasp=False,
                role="PAYMENT",
                source="ransomwhere",
                source_url=source_url,
                reliability=0.80,
                tier="B",
                retrieved_at=retrieved,
                evidence={"family": family, "first_seen": item.get("createdAt"), "dataset": "Ransomwhere"},
            )
        )
    return rows, stats


def fetch(from_dir: Path | None = None, file: Path | None = None) -> IngestResult:
    retrieved = iso(utcnow()) or ""
    if file is not None or from_dir is not None:
        path = file if file is not None else (from_dir / FILE_NAME)  # type: ignore[operator]
        text = path.read_text(encoding="utf-8")
        source_url = f"{HOME} (Zenodo dataset file {FILE_NAME}, ingested from local copy)"
        version = None
    else:
        record_id = find_record_id(http_get_text(HOME))
        if record_id is None:
            raise RuntimeError("Could not find the Zenodo record link on https://ransomwhe.re")
        record = http_get_json(ZENODO_API.format(id=record_id))
        latest = (record.get("links") or {}).get("latest")
        if latest:
            record = http_get_json(latest)
        url = _file_url(record)
        if url is None:
            raise RuntimeError(f"Zenodo record {record.get('id')} has no {FILE_NAME}")
        text = http_get_text(url, timeout=180)
        source_url = f"https://zenodo.org/records/{record.get('id')}"
        version = str(record.get("id"))
    rows, stats = parse(json.loads(text), source_url, retrieved)
    return IngestResult(
        source="ransomwhere",
        rows=rows,
        source_url=source_url,
        version=version,
        content_sha256=content_hash(text),
        notes=stats,
    )
