# Demo cases

Not recorded yet. The five cases must come from real chain data:

```
cd backend
python scripts/build_ground_truth.py     # finds them (needs ETHERSCAN_API_KEY, TRONGRID_API_KEY recommended)
python scripts/record_fixtures.py        # records them and rewrites this file with measured results
```

| # | Case | Chain | Expected result |
|---|---|---|---|
| 1 | Direct deposit to an inferred exchange deposit address | Ethereum | nearest VASP at 1 hop via inferred deposit address |
| 2 | Multi-hop USDT-TRC20 path to a labelled exchange | TRON | nearest VASP at 2–3 hops |
| 3 | Bitcoin case resolved by common-input clustering | Bitcoin | nearest VASP via common-input cluster |
| 4 | Path ending at a mixer or bridge breakpoint | Ethereum | breakpoint reported; MIXER_EXPOSURE or CROSS_CHAIN_BREAKPOINT |
| 5 | Honest no-match | any | no VASP at confidence ≥ 50, stated plainly |

`record_fixtures.py` replaces this table with the actual wallets and the measured nearest
VASP, confidence, tier, hops and risk for each case, and confirms that replay reproduces the
recorded result.
