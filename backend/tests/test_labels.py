"""Label ingestion (parsers on synthetic fixtures; counts on the real committed seed)
and LabelStore lookups."""
from __future__ import annotations

import csv
from collections import Counter

from app.config import SEED_CSV
from app.db import session_scope
from app.labels.sources import defillama_cex, eth_labels, ofac, ransomwhere
from app.labels.store import IngestResult, LabelRow, LabelStore, read_seed_csv, replace_source
from app.providers.context import AnalysisContext, use_context
from tests.helpers import FIXTURES


def test_seed_csv_has_real_labels_for_each_bulk_source():
    counts = Counter()
    with SEED_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            counts[row["source"]] += 1
            assert row["source_url"].startswith("https://"), row
            assert row["retrieved_at"], row
    assert counts["ofac"] > 0
    assert counts["eth_labels"] > 0
    assert counts["defillama_cex"] > 0


def test_seed_csv_tiers_match_sources():
    tiers = {"ofac": ("A", 0.95), "defillama_cex": ("A", 0.95), "eth_labels": ("B", 0.80)}
    for source, rows in read_seed_csv(SEED_CSV).items():
        exp = tiers.get(source)
        if exp:
            assert {(r.tier, r.reliability) for r in rows} == {exp}
        if source == "ofac":
            assert {r.entity_type for r in rows} == {"SANCTIONED"}


def test_ofac_parser():
    res = ofac.fetch(from_dir=FIXTURES / "synthetic_ofac")
    by = {(r.chain, r.address) for r in res.rows}
    assert ("bitcoin", "bc1q5xs6rgdp5xs6rgdp5xs6rgdp5xs6rgdp4gkthz") in by
    assert ("tron", "TWvo9apiTq74uE5XQwnjt5zGgQ7vUayejH") in by  # listed under XBT, re-assigned by format
    assert ("ethereum", "0xabcdef0123456789abcdef0123456789abcdef01") in by  # lower-cased
    assert ("tron", "TVNZDWY6YpQcK8Y41ehav5W9mhW8HQGynR") in by  # USDT list mapped by format
    assert res.notes["rejected_invalid"] == 1
    assert all(r.tier == "A" and r.entity_type == "SANCTIONED" for r in res.rows)


def test_eth_labels_parser_data_driven_exchange_detection():
    res = eth_labels.fetch(from_dir=FIXTURES / "synthetic_eth_labels")
    rows = {(r.chain, r.address, r.entity_type): r for r in res.rows}
    hot = rows[("ethereum", "0x00000000000000000000000000000000000000a2", "CEX")]
    assert hot.entity_name == "Acmex" and hot.role == "HOT"
    funder = rows[("ethereum", "0x00000000000000000000000000000000000000a3", "CEX")]
    assert funder.role == "OPERATOR"
    dep = rows[("ethereum", "0x00000000000000000000000000000000000000d1", "CEX")]
    assert dep.role == "DEPOSIT" and dep.entity_name == "Acmex"
    mixer = rows[("ethereum", "0x00000000000000000000000000000000000000b1", "MIXER")]
    assert mixer.entity_name == "Tornado.Cash"
    assert rows[("ethereum", "0x00000000000000000000000000000000000000c1", "SCAM")]
    assert not any(r.address.endswith("e1") for r in res.rows)  # uniswap: not a kept category
    generic = rows[("avalanche", "0x00000000000000000000000000000000000000f1", "CEX")]
    assert generic.entity_name.startswith("Unnamed exchange")
    assert res.notes["skipped_invalid"] == 1


def test_defillama_parser_static_owners_only():
    res = defillama_cex.fetch(from_dir=FIXTURES / "synthetic_defillama")
    got = {(r.entity_name, r.chain, r.address) for r in res.rows}
    assert ("Acmex", "ethereum", "0x00000000000000000000000000000000000000a1") in got
    assert ("Acmex", "bitcoin", "bc1qk2et9v4jk2et9v4jk2et9v4jk2et9v4jhzxjr8") in got  # via bitcoin-book key
    assert ("Acmex", "tron", "TQhqRHgEonKEYqudomS8243o3bejg8dt1d") in got
    assert ("Beta", "ethereum", "0x00000000000000000000000000000000000000b2") in got  # const reference
    assert ("Beta", "ethereum", "0x00000000000000000000000000000000000000b3") in got  # spread reference
    assert ("Gamma", "polygon", "0x00000000000000000000000000000000000000c3") in got  # cex/<file>.js
    assert ("Zeta", "bitcoin", "bc1q6n2df4x56n2df4x56n2df4x56n2df4x56nessm") in got  # imported book file
    assert ("Zeta", "bsc", "0x00000000000000000000000000000000000000d4") in got
    addresses = {a for _n, _c, a in got}
    assert "0x0000000000000000000000000000000000000099" not in addresses  # commented out
    assert not any("ripple" == c for _n, c, _a in got)
    assert "dynamic-ex" in res.notes["dynamic_or_empty"]
    acme = next(r for r in res.rows if r.entity_name == "Acmex")
    assert acme.evidence["methodology"] == "Synthetic methodology text"
    assert acme.tier == "A" and acme.reliability == 0.95 and acme.role == "HOT"
    assert res.notes["rejected_invalid"] >= 2


def test_ransomwhere_parser():
    res = ransomwhere.fetch(file=FIXTURES / "synthetic_ransomwhere.json")
    assert len(res.rows) == 1
    row = res.rows[0]
    assert row.entity_type == "RANSOMWARE" and row.entity_name == "SyntheticLocker (ransomware)" and row.tier == "B"
    assert res.notes == {"items": 3, "rejected_invalid": 1, "unsupported_chain": 1}


def test_ransomwhere_record_link_detection():
    assert ransomwhere.find_record_id('<a href="https://zenodo.org/records/1234567">data</a>') == "1234567"
    assert ransomwhere.find_record_id("doi 10.5281/zenodo.7654321") == "7654321"


def _row(chain, addr, name, etype, source, rel=0.8, tier="B", role="HOT"):
    return LabelRow(chain, addr, name, etype, etype == "CEX", role, source, "https://example.invalid/src", rel, tier, "2026-01-01T00:00:00Z", {})


def test_replace_source_and_lookup_cross_evm_and_hidden():
    a = "0x" + "5a" * 20
    contract = "0x" + "5b" * 20
    with session_scope() as db:
        replace_source(
            db,
            IngestResult(
                source="synthetic_test",
                rows=[
                    _row("avalanche", a, "Synthex", "CEX", "synthetic_test"),
                    _row("ethereum", a, "Synthex", "CEX", "synthetic_test", rel=0.95, tier="A"),
                    _row("avalanche", contract, "Synthex", "CEX", "synthetic_test", role="CONTRACT"),
                ],
                source_url=None,
                version="t",
            ),
        )
    with session_scope() as db:
        store = LabelStore(db)
        hits = store.lookup("ethereum", a.upper().replace("0X", "0x"))
        assert hits[0].reliability == 0.95 and hits[0].inherited_from is None
        poly = store.lookup("polygon", a)
        assert {h.inherited_from for h in poly} == {"ethereum", "avalanche"}
        assert max(h.reliability for h in poly) == round(0.95 * 0.9, 4)
        assert store.lookup("ethereum", contract) == []  # contracts are not carried across chains
    with session_scope() as db:
        ctx = AnalysisContext(analysis_id="t", hidden_label_addresses={("ethereum", a)})
        with use_context(ctx):
            assert LabelStore(db).lookup("ethereum", a) == []
