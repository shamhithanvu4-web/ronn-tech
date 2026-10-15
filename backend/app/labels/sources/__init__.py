"""Label sources. Bulk sources expose `fetch(from_dir=None, file=None) -> IngestResult`;
live sources expose async lookups used by the engine (cached and recorded)."""

BULK_SOURCES = ("ofac", "eth_labels", "defillama_cex", "ransomwhere")
LIVE_SOURCES = ("tronscan_tags", "walletexplorer", "chainalysis_sanctions")
