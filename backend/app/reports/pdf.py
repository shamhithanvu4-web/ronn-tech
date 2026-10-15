"""PDF rendering with ReportLab (works on Windows, macOS and Linux; no system libraries).

The content SHA-256 is printed on the cover and embedded in the PDF metadata
(keywords "ronn-content-sha256:<hex>") so a downloaded copy can be matched back.
"""
from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import PRODUCT_NAME

NAVY = colors.HexColor("#0B1F3A")
MUTED = colors.HexColor("#5C6B7F")
BORDER = colors.HexColor("#E3E9F2")
TINT = colors.HexColor("#EEF3FA")
META_PREFIX = "ronn-content-sha256:"


def t(value: Any) -> str:
    """Escape for ReportLab mini-markup and drop characters the base fonts cannot draw."""
    s = "" if value is None else str(value)
    s = s.encode("cp1252", errors="replace").decode("cp1252")
    return escape(s)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], textColor=NAVY, fontSize=18, leading=22, alignment=TA_LEFT),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], textColor=NAVY, fontSize=13, leading=16, spaceBefore=10, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], textColor=NAVY, fontSize=10.5, leading=13, spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=8.5, leading=11),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=7, leading=9, textColor=MUTED),
        "mono": ParagraphStyle("mono", parent=base["BodyText"], fontName="Courier", fontSize=7, leading=8.5, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontSize=7, leading=8.5, wordWrap="CJK"),
    }


def _table(rows: list[list[Any]], widths: list[float], header: bool = True) -> Table:
    tbl = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), TINT), ("TEXTCOLOR", (0, 0), (-1, 0), NAVY)]
    tbl.setStyle(TableStyle(style))
    return tbl


def _kv(st, pairs: list[tuple[str, Any]], mono_keys: tuple[str, ...] = ()) -> Table:
    rows = [
        [Paragraph(f"<b>{t(k)}</b>", st["cell"]), Paragraph(t(v), st["mono"] if k in mono_keys else st["cell"])]
        for k, v in pairs
    ]
    return _table(rows, [45 * mm, 130 * mm], header=False)


def _footer(canvas, doc, label: str) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 10 * mm, f"{PRODUCT_NAME} · {label} · Attribution is an investigative lead, not proof of ownership.")
    canvas.drawRightString(195 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _doc(buf: io.BytesIO, title: str, content_hash: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=16 * mm,
        title=title,
        author=PRODUCT_NAME,
        subject=f"{PRODUCT_NAME} report",
        keywords=f"{META_PREFIX}{content_hash}",
        creator=PRODUCT_NAME,
    )


def render_analysis_pdf(content: dict[str, Any], content_hash: str, integrity: dict[str, Any]) -> bytes:
    st = _styles()
    story: list[Any] = []
    cov = content["cover"]
    summ = content["summary"]
    nv = summ.get("likely_deposit_destination")

    # Cover
    story.append(Paragraph(f"{t(PRODUCT_NAME)} · Wallet attribution report", st["title"]))
    story.append(Spacer(1, 4))
    story.append(
        _kv(
            st,
            [
                ("Case number", cov.get("case_no") or "Not linked to a case"),
                ("Case title", cov.get("case_title")),
                ("Complaint ref / FIR", f"{cov.get('complaint_ref') or '-'} / {cov.get('fir_no') or '-'}"),
                ("Agency", cov.get("agency")),
                ("Analysis ID", cov.get("analysis_id")),
                ("Generated (IST)", cov.get("generated_at_ist")),
                ("Generated (UTC)", cov.get("generated_at_utc")),
                ("Analyst", cov.get("analyst")),
                ("Data mode", cov.get("data_mode")),
                ("Report SHA-256", content_hash),
            ],
            mono_keys=("Report SHA-256", "Analysis ID"),
        )
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph(t(content["disclaimer"]), st["small"]))

    # Summary
    story.append(Paragraph("1. Summary", st["h1"]))
    subj = content["subject"]
    pairs = [("Suspect wallet", subj["address"]), ("Address family", subj.get("family"))]
    if nv:
        pairs += [
            ("Likely deposit destination", f"{nv['entity_name']} ({nv['entity_type']})"),
            ("Attribution confidence", f"{nv['confidence']:.1f} / 100 · {nv['tier']}"),
            ("Hops", nv["hops"]),
            ("Via", f"{nv['via']} {nv['target_address']} ({nv['chain']})"),
        ]
    else:
        pairs.append(("Likely deposit destination", "None at attribution confidence >= 50"))
    pairs += [
        ("Risk level", summ.get("risk_level")),
        ("Recommended action", summ.get("recommended_action")),
        ("Window (IST)", f"{subj.get('window_start_ist') or '-'} to {subj.get('window_end_ist') or '-'}"),
        ("Result partial", "Yes: " + "; ".join(summ.get("partial_reasons") or []) if summ.get("partial") else "No"),
    ]
    story.append(_kv(st, pairs, mono_keys=("Suspect wallet",)))
    chains = [[Paragraph("<b>Chain</b>", st["cell"]), Paragraph("<b>Status</b>", st["cell"]), Paragraph("<b>Detail</b>", st["cell"])]]
    for c in subj.get("chains_checked", []):
        chains.append([Paragraph(t(c["chain"]), st["cell"]), Paragraph(t(c["status"]), st["cell"]), Paragraph(t(c.get("detail") or ""), st["cell"])])
    story.append(Spacer(1, 4))
    story.append(_table(chains, [30 * mm, 35 * mm, 110 * mm]))

    # Candidates
    story.append(Paragraph("2. Candidates and factor breakdown", st["h1"]))
    if not content["candidates"]:
        story.append(Paragraph("No labelled entity was reached within the traced paths.", st["body"]))
    for i, c in enumerate(content["candidates"], 1):
        f = c["factors"]
        block = [
            Paragraph(f"2.{i} {t(c['entity_name'])} ({t(c['entity_type'])}) - confidence {c['attribution_confidence']:.1f} ({t(c['tier'])})", st["h2"]),
            _kv(
                st,
                [
                    ("Routable", "Yes" if c["routable"] else "No (confidence below 50 or not a VASP)"),
                    ("Nearest hops / paths", f"{c['min_hops']} / {c['path_count']}"),
                    ("Best path factors", f"R {f['R']:.2f} · H {f['H']:.2f} · V {f['V']:.4f} · T {f['T']:.1f}"),
                    ("Via", c["via"]),
                    ("Label sources", ", ".join(c["label_sources"])),
                    ("What would raise this?", c["what_would_raise_this"]),
                ],
            ),
        ]
        story.append(KeepTogether(block))

    # Evidence paths
    story.append(Paragraph("3. Evidence paths", st["h1"]))
    for i, c in enumerate(content["candidates"], 1):
        for j, p in enumerate(c["evidence_paths"], 1):
            story.append(
                Paragraph(
                    f"3.{i}.{j} {t(c['entity_name'])}: {p['hops']} hop(s), path score {p['score']:.4f}, via {t(p['via'])}; "
                    f"label source {t(p['label']['source'])} (tier {t(p['label']['tier'])}, retrieved {t(p['label']['retrieved_at'])})",
                    st["h2"],
                )
            )
            rows = [[Paragraph(f"<b>{h}</b>", st["cell"]) for h in ("From", "To", "Chain", "Asset / amount", "Time (UTC / IST)", "Tx hash")]]
            if not p["transfers"]:
                rows.append([Paragraph("Hop 0: the suspect itself is the attributed address.", st["cell"]), "", "", "", "", ""])
            for tr in p["transfers"]:
                rows.append(
                    [
                        Paragraph(t(tr["from"]), st["mono"]),
                        Paragraph(t(tr["to"]), st["mono"]),
                        Paragraph(t(tr["chain"]), st["cell"]),
                        Paragraph(t(f"{tr['amount']} {tr['asset']}" + (f" (~USD {tr['usd']})" if tr.get("usd") else "")), st["cell"]),
                        Paragraph(t(f"{tr['time_utc']}<br/>{tr['time_ist']}").replace("&lt;br/&gt;", "<br/>"), st["cell"]),
                        Paragraph(f'{t(tr["tx_hash"])}<br/><font color="#2563EB">{t(tr["explorer_url"] or "")}</font>', st["mono"]),
                    ]
                )
            story.append(_table(rows, [28 * mm, 28 * mm, 14 * mm, 28 * mm, 30 * mm, 52 * mm]))
            story.append(Spacer(1, 3))
    deps = content.get("deposit_inferences") or []
    if deps:
        story.append(Paragraph("Inferred deposit addresses (tier C)", st["h2"]))
        for d in deps:
            story.append(
                _kv(
                    st,
                    [
                        ("Deposit address", f"{d['deposit_address']} ({d['chain']})"),
                        ("Entity", d["entity_name"]),
                        ("Reliability / corroborations", f"{d['reliability']:.2f} / {', '.join(d['corroborations']) or 'sweep only'}"),
                        ("Deposit txs", ", ".join(d["evidence"]["deposit_txs"])),
                        ("Sweep txs", ", ".join(d["evidence"]["sweep_txs"])),
                        ("Gas top-up txs", ", ".join(d["evidence"]["gas_topup_txs"]) or "-"),
                        ("Linked wallets (shared deposit address)", ", ".join(d.get("linked_wallets") or []) or "-"),
                    ],
                    mono_keys=("Deposit address", "Deposit txs", "Sweep txs", "Gas top-up txs", "Linked wallets (shared deposit address)"),
                )
            )
    if content.get("funding_sources"):
        story.append(Paragraph("Funding-source VASPs", st["h2"]))
        for fsrc in content["funding_sources"]:
            story.append(Paragraph(t(f"{fsrc['entity_name']}: confidence {fsrc['attribution_confidence']:.1f} ({fsrc['tier']}), {fsrc['min_hops']} hop(s)"), st["body"]))

    # Typologies and risk
    story.append(Paragraph("4. Typologies and risk", st["h1"]))
    risk = content.get("risk", {})
    story.append(Paragraph(f"<b>Risk level: {t(risk.get('level'))}</b>", st["body"]))
    for rsn in risk.get("reasons", []):
        story.append(Paragraph(t(f"[{rsn['level']}] {rsn['reason']}" + (f" - txs: {', '.join(rsn['evidence_txs'])}" if rsn.get("evidence_txs") else "")), st["body"]))
    if content["typologies"]:
        rows = [[Paragraph(f"<b>{h}</b>", st["cell"]) for h in ("Typology", "Description", "Evidence transactions")]]
        for ty in content["typologies"]:
            rows.append([Paragraph(t(ty["title"]), st["cell"]), Paragraph(t(ty["description"]), st["cell"]), Paragraph(t(", ".join(ty["evidence_txs"][:12])), st["mono"])])
        story.append(_table(rows, [30 * mm, 65 * mm, 85 * mm]))
    else:
        story.append(Paragraph("No typologies detected.", st["body"]))
    bps = (content.get("cross_chain") or {}).get("breakpoints") or []
    for bp in bps:
        story.append(Paragraph(t(f"Cross-chain breakpoint: {bp['bridge']} at {bp['address']} ({bp['chain']}), {bp['amounts']}. {bp['recommendation']}"), st["body"]))

    # Methodology
    story.append(Paragraph("5. Methodology and limitations", st["h1"]))
    for m in content["methodology"]:
        story.append(Paragraph(f"• {t(m)}", st["body"]))
    story.append(Paragraph("Limitations", st["h2"]))
    for m in content["limitations"]:
        story.append(Paragraph(f"• {t(m)}", st["body"]))

    # Data sources
    story.append(Paragraph("6. Data sources", st["h1"]))
    rows = [[Paragraph(f"<b>{h}</b>", st["cell"]) for h in ("Label source", "Tier", "Retrieved", "URL")]]
    for ls in content["data_sources"]["labels"]:
        rows.append([Paragraph(t(ls["source"]), st["cell"]), Paragraph(t(ls["tier"]), st["cell"]), Paragraph(t(ls["retrieved_at"]), st["cell"]), Paragraph(t(ls["source_url"]), st["mono"])])
    story.append(_table(rows, [30 * mm, 10 * mm, 32 * mm, 108 * mm]))
    story.append(Paragraph(t(content["data_sources"]["usd_note"]), st["small"]))

    # Integrity
    story.append(PageBreak())
    story.append(Paragraph("7. Integrity", st["h1"]))
    story.append(
        _kv(
            st,
            [
                ("Report content SHA-256", integrity["content_sha256"]),
                ("Analysis JSON SHA-256", integrity.get("analysis_sha256")),
                ("Algorithm", integrity["algorithm"]),
            ],
            mono_keys=("Report content SHA-256", "Analysis JSON SHA-256"),
        )
    )
    rows = [[Paragraph(f"<b>{h}</b>", st["cell"]) for h in ("Raw provider response (URL, API keys removed)", "SHA-256", "Retrieved (UTC)")]]
    for e in integrity["raw_responses"][:400]:
        rows.append([Paragraph(t(e["url"]), st["mono"]), Paragraph(t(e["sha256"]), st["mono"]), Paragraph(t(e["retrieved_at"]), st["cell"])])
    if len(integrity["raw_responses"]) > 400:
        rows.append([Paragraph(t(f"... {len(integrity['raw_responses']) - 400} more in the JSON export"), st["cell"]), "", ""])
    story.append(_table(rows, [85 * mm, 65 * mm, 30 * mm]))

    # Certificate
    cert = content["certificate"]
    story.append(Paragraph("8. " + t(cert["title"]), st["h1"]))
    story.append(Paragraph(t(cert["status"]), st["small"]))
    for fld in cert["fields"]:
        story.append(Paragraph(t(fld), st["body"]))
        story.append(Spacer(1, 3))
    story.append(Paragraph(t(cert["note"]), st["small"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(t(content["disclaimer"]), st["small"]))

    buf = io.BytesIO()
    doc = _doc(buf, f"{PRODUCT_NAME} attribution report {cov.get('analysis_id')}", content_hash)
    doc.build(story, onFirstPage=lambda c, d: _footer(c, d, "Attribution report"), onLaterPages=lambda c, d: _footer(c, d, "Attribution report"))
    return buf.getvalue()


def render_request_pdf(content: dict[str, Any], content_hash: str) -> bytes:
    st = _styles()
    story: list[Any] = []
    req = content["request"]
    story.append(Paragraph(f"{t(PRODUCT_NAME)} · Lawful request ({t(req['request_type'].replace('_', ' ').title())})", st["title"]))
    if content.get("simulated"):
        story.append(Paragraph("<b>SIMULATED</b> - SAHYOG integration is a documented, simulated adapter. Nothing has been sent to any real system.", st["small"]))
    story.append(Spacer(1, 4))
    story.append(
        _kv(
            st,
            [
                ("Request number", req["request_no"]),
                ("Status", req["status"]),
                ("Case number", content["case"]["case_no"]),
                ("Complaint ref / FIR", f"{content['case'].get('complaint_ref') or '-'} / {content['case'].get('fir_no') or '-'}"),
                ("Agency", content["case"].get("agency")),
                ("Addressed to (VASP)", req.get("entity_name") or "-"),
                ("Routing", f"{req['routing']} - {req.get('routing_reason') or ''}"),
                ("Legal basis", req["legal_basis_label"]),
                ("Drafted by / at (IST)", f"{req['drafted_by']} / {req['drafted_at_ist']}"),
                ("Approved by / at (IST)", f"{req.get('approved_by') or '-'} / {req.get('approved_at_ist') or '-'}"),
                ("Dispatched (IST)", req.get("dispatched_at_ist") or "-"),
                ("Request SHA-256", content_hash),
            ],
            mono_keys=("Request SHA-256",),
        )
    )
    story.append(Paragraph(t(content["legal_note"]), st["small"]))
    story.append(Paragraph("Subject wallet and evidence", st["h1"]))
    ev = content["evidence"]
    story.append(
        _kv(
            st,
            [
                ("Suspect wallet", ev.get("suspect_address")),
                ("Deposit / receiving address at the VASP", ev.get("target_address")),
                ("Chain", ev.get("chain")),
                ("Attribution confidence", f"{ev.get('confidence')} ({ev.get('tier')})"),
                ("Via", ev.get("via")),
                ("Analysis ID / SHA-256", f"{ev.get('analysis_id')} / {ev.get('analysis_sha256')}"),
                ("Key transactions", ", ".join(ev.get("tx_hashes") or [])),
            ],
            mono_keys=("Suspect wallet", "Deposit / receiving address at the VASP", "Analysis ID / SHA-256", "Key transactions"),
        )
    )
    story.append(Paragraph("Information requested", st["h1"]))
    for item in content["items_requested"]:
        story.append(Paragraph(f"• {t(item)}", st["body"]))
    if content.get("notes"):
        story.append(Paragraph("Officer notes", st["h2"]))
        story.append(Paragraph(t(content["notes"]), st["body"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(t(content["disclaimer"]), st["small"]))
    buf = io.BytesIO()
    doc = _doc(buf, f"{PRODUCT_NAME} lawful request {req['request_no']}", content_hash)
    doc.build(story, onFirstPage=lambda c, d: _footer(c, d, "Lawful request"), onLaterPages=lambda c, d: _footer(c, d, "Lawful request"))
    return buf.getvalue()


def embedded_content_hash(pdf_bytes: bytes) -> str | None:
    """Read the content hash from the PDF metadata keywords (best effort, no extra deps)."""
    marker = META_PREFIX.encode()
    i = pdf_bytes.find(marker)
    if i < 0:
        return None
    j = i + len(marker)
    h = pdf_bytes[j : j + 64]
    try:
        s = h.decode("ascii")
    except UnicodeDecodeError:
        return None
    return s if all(c in "0123456789abcdef" for c in s) and len(s) == 64 else None
