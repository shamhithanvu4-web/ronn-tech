"""Exchange proof-of-reserve wallets from DefiLlama CEX adapters (tier A).

Live mode (SPEC §5):
  1. GET https://api.llama.fi/protocols, keep category == "CEX";
  2. for each protocol's "module", fetch the adapter from
     https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/main/projects/<module>
     (falling back to the repo's top-level cex/ folder, where most CEX configs now live);
  3. extract the static wallet ("owners") lists per chain key and validate each address.
Offline mode (--from-dir <clone>): the CEX set is every config in cex/index.js, every
cex/<name>.js file and every projects/* adapter that uses helper/cex.

Adapters that load their wallets at runtime from an exchange endpoint (e.g. a
monthly PoR CSV) have no static addresses; they are reported, not guessed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from app.db import iso, utcnow
from app.engine.validate import validate_address
from app.errors import InvalidAddress
from app.labels.sources.common import content_hash, git_head, http_get_json, http_get_text
from app.labels.store import IngestResult, LabelRow

REPO = "DefiLlama/DefiLlama-Adapters"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/{{path}}"
BLOB = f"https://github.com/{REPO}/blob/{{ref}}/{{path}}"
PROTOCOLS_URL = "https://api.llama.fi/protocols"
CHAIN_KEYS = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "tron": "tron",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "solana": "solana",
}
HINTS = {"bitcoin": "bitcoin", "tron": "tron", "solana": "solana"}
IDENT = r"[A-Za-z_$][\w$]*"


# ------------------------------------------------------------------ JS literal helpers
def strip_comments(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i : j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def match_close(src: str, i: int) -> int:
    """Index of the bracket closing the one at src[i] (skips strings)."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    stack = [pairs[src[i]]]
    j = i + 1
    n = len(src)
    while j < n and stack:
        c = src[j]
        if c in "'\"`":
            k = j + 1
            while k < n and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
        j += 1
    return j - 1


def split_top(body: str) -> list[str]:
    """Split an object/array body on top-level commas."""
    parts, depth, cur, i, n = [], 0, [], 0, len(body)
    while i < n:
        c = body[i]
        if c in "'\"`":
            k = i + 1
            while k < n and body[k] != c:
                k += 2 if body[k] == "\\" else 1
            cur.append(body[i : k + 1])
            i = k + 1
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def object_entries(obj_text: str) -> list[tuple[str, str]]:
    """(key, value-text) pairs of a JS object literal '{...}'. Shorthand keys map to themselves."""
    obj_text = obj_text.strip()
    if not obj_text.startswith("{"):
        return []
    body = obj_text[1 : match_close(obj_text, 0)]
    entries = []
    for part in split_top(body):
        if part.startswith("..."):
            entries.append(("...", part[3:].strip()))
            continue
        m = re.match(r"""^(?:(['"])(?P<q>[^'"]+)\1|(?P<k>""" + IDENT + r"""|\d+))\s*:\s*(?P<v>.*)$""", part, re.S)
        if m:
            entries.append((m.group("q") or m.group("k"), m.group("v").strip()))
        elif re.fullmatch(IDENT, part):
            entries.append((part, part))
    return entries


def string_literals(text: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"""(['"])((?:(?!\1).)*)\1""", text)]


class ModuleScope:
    """Constant arrays / objects declared in one JS file, resolved lazily."""

    def __init__(self, src: str, book: Callable[[str], list[str]] | None = None):
        self.src = strip_comments(src)
        self.book = book or (lambda _k: [])
        self.decls: dict[str, str] = {}
        for m in re.finditer(r"(?:const|let|var)\s+(" + IDENT + r")\s*=\s*([\[{])", self.src):
            start = m.start(2)
            end = match_close(self.src, start)
            self.decls.setdefault(m.group(1), self.src[start : end + 1])

    def resolve_list(self, expr: str, depth: int = 0) -> list[str]:
        expr = expr.strip().rstrip(";")
        if depth > 6 or not expr:
            return []
        if expr.startswith("["):
            out: list[str] = []
            body = expr[1 : match_close(expr, 0)]
            for item in split_top(body):
                if item.startswith("..."):
                    out.extend(self.resolve_list(item[3:], depth + 1))
                elif item[:1] in "'\"`":
                    out.extend(string_literals(item))
                else:
                    out.extend(self.resolve_list(item, depth + 1))
            return out
        if expr[:1] in "'\"":
            lits = string_literals(expr)
            return self.book(lits[0]) if lits else []
        m = re.fullmatch(r"(?:bitcoinAddressBook|bitcoinBook)\.(" + IDENT + r")", expr)
        if m:
            return self.book(m.group(1))
        if re.fullmatch(IDENT, expr) and expr in self.decls and self.decls[expr].startswith("["):
            return self.resolve_list(self.decls[expr], depth + 1)
        return []

    def resolve_object(self, expr: str) -> str | None:
        expr = expr.strip().rstrip(";")
        if expr.startswith("{"):
            return expr[: match_close(expr, 0) + 1]
        if re.fullmatch(IDENT, expr) and expr in self.decls and self.decls[expr].startswith("{"):
            return self.decls[expr]
        return None

    def chain_owners(self, obj_text: str) -> dict[str, list[str]]:
        """Map chain -> owner address strings for a cexExports-style config object."""
        result: dict[str, list[str]] = {}
        for key, value in object_entries(obj_text):
            if key not in CHAIN_KEYS:
                continue
            owners: list[str] = []
            inner = self.resolve_object(value)
            if inner is not None:
                for k2, v2 in object_entries(inner):
                    if k2 == "owners":
                        owners.extend(self.resolve_list(v2))
            else:
                owners.extend(self.resolve_list(value))
            if owners:
                result.setdefault(CHAIN_KEYS[key], []).extend(owners)
        return result

    def exported_object(self) -> str | None:
        m = re.search(r"module\.exports\s*=\s*", self.src)
        if not m:
            return None
        return self.resolve_object(self.src[m.end() :].lstrip())


# ------------------------------------------------------------------ bitcoin address book
class BitcoinBook:
    def __init__(self, read: Callable[[str], str | None]):
        self.read = read
        self.entries: dict[str, list[str]] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        src = self.read("projects/helper/bitcoin-book/index.js")
        if not src:
            return
        scope = ModuleScope(src)
        for m in re.finditer(r"""\[\s*(['"])(\w+)\1\s*,\s*(['"])\./([\w.-]+)\3\s*\]""", scope.src):
            sub = self.read(f"projects/helper/bitcoin-book/{m.group(4)}")
            if sub:
                exported = re.search(r"module\.exports\s*=\s*", strip_comments(sub))
                if exported:
                    s = strip_comments(sub)
                    self.entries[m.group(2)] = ModuleScope(sub).resolve_list(s[exported.end() :].lstrip())
        exported = scope.exported_object()
        if exported:
            for key, value in object_entries(exported):
                if key == "...":
                    continue
                vals = scope.resolve_list(value)
                if vals:
                    self.entries[key] = vals

    def get(self, key: str) -> list[str]:
        self.load()
        return list(self.entries.get(key, []))


# ------------------------------------------------------------------ module discovery
def _pretty(slug: str) -> str:
    s = re.sub(r"[-_](cex|exchange)$", "", slug)
    return " ".join(w if w.isupper() else w.capitalize() for w in re.split(r"[-_]", s) if w)


def _validate(chain: str, addr: str) -> str | None:
    try:
        hint = HINTS.get(chain, "evm" if addr.lower().startswith("0x") else None)
        info = validate_address(addr, hint)
    except InvalidAddress:
        return None
    if chain in ("ethereum", "bsc", "polygon", "arbitrum") and info.family != "evm":
        return None
    return info.address


def _collect(
    configs: list[tuple[str, str, str, str | None, str | None]], book: BitcoinBook, source_url_for, retrieved
) -> tuple[list[LabelRow], dict[str, Any]]:
    """configs: (slug, display_name, file_path, config_text, methodology)."""
    rows: list[LabelRow] = []
    stats: dict[str, Any] = {"entities": 0, "dynamic_or_empty": [], "rejected_invalid": 0}
    for slug, name, path, text, methodology in configs:
        if text is None:
            stats["dynamic_or_empty"].append(slug)
            continue
        scope_src, obj = text
        scope = ModuleScope(scope_src, book.get)
        owners = scope.chain_owners(obj) if obj else {}
        if not owners:
            stats["dynamic_or_empty"].append(slug)
            continue
        stats["entities"] += 1
        for chain, addrs in owners.items():
            for raw in dict.fromkeys(a.strip() for a in addrs if a and a.strip()):
                addr = _validate(chain, raw)
                if addr is None:
                    stats["rejected_invalid"] += 1
                    continue
                rows.append(
                    LabelRow(
                        chain=chain,
                        address=addr,
                        entity_name=name,
                        entity_type="CEX",
                        is_vasp=True,
                        role="HOT",
                        source="defillama_cex",
                        source_url=source_url_for(path),
                        reliability=0.95,
                        tier="A",
                        retrieved_at=retrieved,
                        evidence={
                            "adapter": path,
                            "config_key": slug,
                            "methodology": methodology,
                            "note": "Wallet listed in the exchange's proof-of-reserve adapter (static owners list).",
                        },
                    )
                )
    stats["dynamic_or_empty"] = sorted(stats["dynamic_or_empty"])
    return rows, stats


def _cex_index_configs(read, book) -> list[tuple[str, str, str, Any, str | None]]:
    out = []
    src = read("cex/index.js")
    if not src:
        return out
    scope = ModuleScope(src, book.get)
    configs_obj = scope.decls.get("configs")
    if configs_obj:
        for slug, value in object_entries(configs_obj):
            obj = scope.resolve_object(value)
            methodology = None
            if obj:
                for k, v in object_entries(obj):
                    if k == "methodology":
                        lits = string_literals(v)
                        methodology = " ".join(lits) if lits else None
            out.append((slug, _pretty(slug), "cex/index.js", (src, obj) if obj else None, methodology))
    files_decl = scope.decls.get("fileConfigs")
    if files_decl:
        for name in string_literals(files_decl):
            fsrc = read(f"cex/{name}.js")
            if not fsrc:
                out.append((name, _pretty(name), f"cex/{name}.js", None, None))
                continue
            fscope = ModuleScope(fsrc, book.get)
            obj = fscope.exported_object()
            methodology = None
            if obj:
                for k, v in object_entries(obj):
                    if k == "methodology":
                        lits = string_literals(v)
                        methodology = " ".join(lits) if lits else None
            out.append((name, _pretty(name), f"cex/{name}.js", (fsrc, obj) if obj else None, methodology))
    return out


def _project_config(src: str, book) -> tuple[str, str] | None:
    scope = ModuleScope(src, book.get)
    if "config" in scope.decls and scope.decls["config"].startswith("{"):
        return (src, scope.decls["config"])
    m = re.search(r"cexExports\(\s*", scope.src)
    if m:
        rest = scope.src[m.end() :]
        if rest.startswith("{"):
            obj = scope.resolve_object(rest)
        else:
            ident = re.match(IDENT, rest)
            obj = scope.resolve_object(ident.group(0)) if ident else None
        if obj:
            return (src, obj)
    return None


# ------------------------------------------------------------------ entry point
def fetch(from_dir: Path | None = None, file: Path | None = None) -> IngestResult:
    retrieved = iso(utcnow()) or ""
    texts: list[str] = []
    notes: dict[str, Any] = {}
    if from_dir is not None:
        ref = git_head(from_dir) or "main"

        def read(path: str) -> str | None:
            p = from_dir / path
            if p.exists():
                t = p.read_text(encoding="utf-8", errors="replace")
                texts.append(t)
                return t
            return None

        book = BitcoinBook(read)
        configs = _cex_index_configs(read, book)
        known = {c[0] for c in configs}
        for path in sorted((from_dir / "projects").glob("*/index.js")):
            src = path.read_text(encoding="utf-8", errors="replace")
            if "helper/cex" not in src:
                continue
            slug = path.parent.name
            if slug in known:
                continue
            texts.append(src)
            cfg = _project_config(src, book)
            configs.append((slug, _pretty(slug), f"projects/{slug}/index.js", cfg, None))
        notes["cex_set_rule"] = "offline: cex/index.js configs + cex/*.js + projects/* using helper/cex"

        def url_for(path: str) -> str:
            return BLOB.format(ref=ref, path=path)

        version = ref
    else:
        cache: dict[str, str | None] = {}

        def read(path: str) -> str | None:
            if path not in cache:
                try:
                    cache[path] = http_get_text(RAW.format(path=path))
                    texts.append(cache[path] or "")
                except RuntimeError:
                    cache[path] = None
            return cache[path]

        book = BitcoinBook(read)
        configs = _cex_index_configs(read, book)
        by_slug = {c[0]: i for i, c in enumerate(configs)}
        protocols = [p for p in http_get_json(PROTOCOLS_URL) if p.get("category") == "CEX"]
        notes["protocols_cex"] = len(protocols)
        for p in protocols:
            module = str(p.get("module") or "")
            slug = module.split("/")[0].replace(".js", "")
            name = p.get("name") or _pretty(slug)
            methodology = p.get("methodology")
            if slug in by_slug:
                old = configs[by_slug[slug]]
                configs[by_slug[slug]] = (old[0], name, old[2], old[3], methodology or old[4])
                continue
            src = read(f"projects/{module}") if module else None
            path = f"projects/{module}"
            if src is None:
                src = read(f"cex/{slug}.js")
                path = f"cex/{slug}.js"
            cfg = None
            if src:
                cfg = _project_config(src, book)
                if cfg is None:
                    obj = ModuleScope(src, book.get).exported_object()
                    cfg = (src, obj) if obj else None
            configs.append((slug, name, path, cfg, methodology))
        notes["cex_set_rule"] = "live: api.llama.fi/protocols category CEX (+ cex/index.js configs)"

        def url_for(path: str) -> str:
            return BLOB.format(ref="main", path=path)

        version = "main"

    rows, stats = _collect(configs, book, url_for, retrieved)
    notes.update(stats)
    return IngestResult(
        source="defillama_cex",
        rows=rows,
        source_url=f"https://github.com/{REPO}",
        version=version,
        content_sha256=content_hash(*texts),
        notes=notes,
    )
