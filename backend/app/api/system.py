"""Health (per-provider status and plan limits) and audit-chain verification."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.config import METHODOLOGY_VERSION, PRODUCT_NAME, get_settings
from app.db import get_db, iso
from app.models import Label, LabelSourceRun, User
from app.providers import health as provider_health
from app.security import get_current_user, require_roles

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    providers = await provider_health()
    labels = dict(db.execute(select(Label.source, func.count()).group_by(Label.source)).all())
    runs = []
    for run in db.execute(select(LabelSourceRun).order_by(LabelSourceRun.id.desc()).limit(20)).scalars():
        runs.append(
            {
                "source": run.source,
                "finished_at": iso(run.finished_at),
                "labels": run.label_count,
                "version": run.version,
                "source_url": run.source_url,
            }
        )
    return {
        "product": PRODUCT_NAME,
        "status": "ok",
        "data_mode": get_settings().data_mode,
        "methodology_version": METHODOLOGY_VERSION,
        "providers": providers,
        "labels_by_source": labels,
        "label_runs": runs,
    }


@router.get("/audit/verify")
def audit_verify(
    db: Session = Depends(get_db), user: User = Depends(require_roles("ADMIN", "SUPERVISOR", "ANALYST"))
) -> dict:
    return verify_chain(db)


@router.get("/methodology")
def methodology(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Everything the /methodology page shows: scoring weights, heuristics with sources,
    label coverage, limitations and the latest measured evaluation (if one was run)."""
    import json

    from app.config import BACKEND_DIR, config_sha256, scoring_config
    from app.reports.content import LIMITATIONS, METHODOLOGY

    tiers = dict(db.execute(select(Label.tier, func.count()).group_by(Label.tier)).all())
    by_source = [
        {"source": s, "tier": t, "labels": n}
        for s, t, n in db.execute(select(Label.source, Label.tier, func.count()).group_by(Label.source, Label.tier)).all()
    ]
    eval_path = BACKEND_DIR / "eval" / "results.json"
    evaluation = None
    if eval_path.exists():
        try:
            evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            evaluation = None
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "scoring": scoring_config(),
        "scoring_config_sha256": config_sha256("scoring.yaml"),
        "steps": METHODOLOGY,
        "limitations": LIMITATIONS,
        "heuristics": HEURISTICS,
        "labels_by_tier": tiers,
        "labels_by_source": sorted(by_source, key=lambda x: (x["tier"], x["source"])),
        "evaluation": evaluation,
    }


HEURISTICS = [
    {
        "name": "Deposit-address inference (sweep test)",
        "applies_to": "EVM chains, TRON",
        "rule": "An unlabelled address that forwards at least (1 - 0.01) of a received amount to an exchange-labelled "
        "address within 3,200 blocks on Ethereum or 11 h elsewhere is inferred to be that exchange's deposit address.",
        "corroboration": "Gas top-up from the exchange shortly before a token sweep; exclusive forwarding to at most 3 exchange addresses.",
        "reliability": "0.70 sweep only · 0.80 with one corroboration · 0.85 with both",
        "source": "F. Victor, 'Address clustering heuristics for Ethereum', Financial Cryptography 2020 (Deposit Address Reuse).",
    },
    {
        "name": "Common-input-ownership clustering",
        "applies_to": "Bitcoin",
        "rule": "Inputs spent together in one transaction are controlled by one entity; an address co-spent with an "
        "exchange-labelled address joins that exchange's cluster. CoinJoin-like transactions (>= 5 equal outputs) are excluded.",
        "corroboration": "Change-output detection only chooses which output to follow; it never assigns labels.",
        "reliability": "0.85",
        "source": "S. Nakamoto, Bitcoin whitepaper §10; S. Meiklejohn et al., 'A Fistful of Bitcoins', IMC 2013.",
    },
    {
        "name": "Proportional (haircut) value share",
        "applies_to": "All chains",
        "rule": "V is the product over hops of (amount sent to the next hop / that node's total outflow in the window).",
        "corroboration": "Square root dampens V so a small but real share still counts.",
        "reliability": "n/a (scoring factor)",
        "source": "Haircut tainting as described by M. Möser, R. Böhme and D. Breuker, 'Towards Risk Scoring of Bitcoin Transactions', FC 2014.",
    },
    {
        "name": "Peel chain, fan-out/in, rapid pass-through, structuring, address poisoning",
        "applies_to": "All chains",
        "rule": "Rule-based typologies with thresholds in config/scoring.yaml; each finding lists its evidence transactions.",
        "corroboration": "Two or more typologies raise wallet risk to Medium.",
        "reliability": "n/a (risk indicators)",
        "source": "FATF, 'Virtual Assets Red Flag Indicators of Money Laundering and Terrorist Financing' (2020).",
    },
]


@router.get("/config/public")
def public_config(user: User = Depends(get_current_user)) -> dict:
    from app.config import chains_config, load_yaml

    chains = {
        c: {
            "family": cfg.get("family"),
            "native": cfg.get("native"),
            "explorer_tx": cfg.get("explorer_tx"),
            "explorer_address": cfg.get("explorer_address"),
        }
        for c, cfg in chains_config().items()
    }
    return {"product": PRODUCT_NAME, "chains": chains, "legal": load_yaml("legal.yaml")}
