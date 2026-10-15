"""Provider registry: one ChainProvider per chain, plus health reporting."""
from __future__ import annotations

from typing import Any

from app.config import chains_config


def make_provider(chain: str) -> Any:
    """Return a fresh provider instance for a chain (instances memoise per analysis)."""
    family = chains_config()[chain]["family"]
    if chain == "bitcoin":
        from app.providers.bitcoin_esplora import BitcoinEsploraProvider

        return BitcoinEsploraProvider()
    if family == "evm":
        from app.providers.evm_etherscan import EtherscanProvider

        return EtherscanProvider(chain)
    if chain == "tron":
        from app.providers.tron_trongrid import TronGridProvider

        return TronGridProvider()
    if chain == "solana":
        from app.providers.solana_helius import SolanaHeliusProvider

        return SolanaHeliusProvider()
    raise KeyError(f"No provider for chain {chain}")


def chain_enabled(chain: str) -> bool:
    if chain == "solana":
        from app.providers.solana_helius import SolanaHeliusProvider

        return SolanaHeliusProvider.enabled()
    return True


async def health() -> list[dict[str, Any]]:
    out = []
    for chain in chains_config():
        prov = make_provider(chain)
        st = await prov.status()
        out.append(st.to_dict())
    return out
