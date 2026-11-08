#!/usr/bin/env python3
"""Recompute the public donation total from Stripe, then regenerate the SVG.

Read-only, stdlib-only. Uses a *restricted* Stripe key (``rk_...``) supplied via
the ``STRIPE_API_KEY`` environment variable. Never commit that key; never log
full API payloads.

What it counts, per the accounting rules in
``../references/accounting-and-security.md``:

    net = sum(paid Checkout Sessions for this Payment Link)
        - successful refunds
        - funds lost to disputes

Checkout Sessions are filtered by the exact ``plink_...`` ID, so unrelated
Stripe revenue is excluded. Amounts are recomputed from source every run, so
replays cannot double count. Currency must match the Payment Link; mixed-currency
totals are refused rather than added.

Usage:
    STRIPE_API_KEY=rk_live_... python3 sync_stripe.py \\
        --config donation.config.json \\
        --output assets/donation-progress.svg

Exit codes: 0 success, 2 bad config/input, 3 Stripe or network error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import render_progress

API_ROOT = "https://api.stripe.com/v1"


class StripeError(RuntimeError):
    pass


def _http_get(path: str, params: dict, api_key: str) -> dict:
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{API_ROOT}/{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "stripe-donations-sync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise StripeError(f"Stripe returned HTTP {exc.code} for {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise StripeError(f"cannot reach Stripe: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise StripeError(f"Stripe returned invalid JSON for {path}") from exc


def _list_all(path: str, params: dict, api_key: str, get=_http_get) -> list[dict]:
    items: list[dict] = []
    starting_after = None
    while True:
        query = dict(params)
        query["limit"] = 100
        if starting_after:
            query["starting_after"] = starting_after
        page = get(path, query, api_key)
        data = page.get("data") or []
        items.extend(data)
        if not page.get("has_more") or not data:
            return items
        starting_after = data[-1].get("id")


def compute_totals(link: dict, api_key: str, get=None) -> dict:
    """Return gross/refunded/disputed/net in minor units plus counts."""
    if get is None:
        get = _http_get
    currency = str(link.get("currency", "")).upper()
    if not currency:
        raise StripeError("Payment Link has no currency")

    sessions = _list_all(
        "checkout/sessions",
        {
            "payment_link": link["id"],
            "status": "complete",
            "expand[]": "data.payment_intent.latest_charge",
        },
        api_key,
        get,
    )

    gross = refunded = disputed = 0
    counted = 0
    for session in sessions:
        if session.get("status") != "complete":
            continue
        if session.get("payment_status") != "paid":
            continue  # pending/async/failed/expired are not contributions
        if str(session.get("currency", "")).upper() != currency:
            raise StripeError(
                "mixed currencies for one Payment Link: "
                f"{currency} vs {session.get('currency')}"
            )
        counted += 1
        gross += int(session.get("amount_total") or 0)

        # Refunds and disputes are properties of the charge, not the session.
        intent = session.get("payment_intent")
        charge = intent.get("latest_charge") if isinstance(intent, dict) else None
        if not isinstance(charge, dict):
            continue
        refunded += max(0, int(charge.get("amount_refunded") or 0))
        if charge.get("disputed"):
            for dispute in _list_all(
                "disputes", {"charge": charge["id"]}, api_key, get
            ):
                # Only "lost" removes funds. "won"/"warning_closed" do not.
                if dispute.get("status") == "lost":
                    disputed += max(0, int(dispute.get("amount") or 0))

    # A refund should never make the public total negative, even if the ledger
    # and charge objects disagree while an event is still settling.
    net = max(0, gross - refunded - disputed)
    return {
        "currency": currency,
        "gross_minor": gross,
        "refunded_minor": refunded,
        "disputed_minor": disputed,
        "raised_minor": net,
        "counted_sessions": counted,
    }


def build_config(existing: dict, totals: dict, livemode: bool, as_of: str) -> dict:
    config = dict(existing)
    config["currency"] = totals["currency"]
    config["raised_minor"] = totals["raised_minor"]
    config["as_of"] = as_of
    # Test-mode or as-yet-unverified totals must stay visibly marked as samples.
    config["sample"] = not livemode
    config.pop("gross_minor", None)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="progress JSON to update")
    parser.add_argument("--output", required=True, type=Path, help="SVG to regenerate")
    parser.add_argument("--plink", help="payment link ID; defaults to config/plink.txt")
    parser.add_argument("--dry-run", action="store_true", help="print totals, change nothing")
    args = parser.parse_args(argv)

    try:
        existing = json.loads(args.config.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read {args.config}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: {args.config} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    plink = args.plink
    if not plink:
        sidecar = args.config.with_name("plink.txt")
        if sidecar.exists():
            plink = sidecar.read_text(encoding="utf-8").strip()
    if not plink or not plink.startswith("plink_"):
        print("error: provide --plink plink_... or a plink.txt sidecar", file=sys.stderr)
        return 2

    api_key = os.environ.get("STRIPE_API_KEY", "").strip()
    if not api_key:
        print("error: set STRIPE_API_KEY to a read-only restricted key", file=sys.stderr)
        return 2
    if api_key.startswith("sk_"):
        print(
            "error: use a restricted read-only key (rk_...), not a secret key",
            file=sys.stderr,
        )
        return 2

    try:
        link = _http_get(f"payment_links/{plink}", {}, api_key)
        if str(link.get("currency", "")).upper() != str(existing.get("currency", "")).upper():
            print(
                "error: config currency "
                f"{existing.get('currency')} does not match link currency "
                f"{str(link.get('currency')).upper()}; update the config first",
                file=sys.stderr,
            )
            return 2
        totals = compute_totals(link, api_key)
    except (StripeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    as_of = dt.datetime.now(dt.timezone.utc).date().isoformat()
    config = build_config(existing, totals, bool(link.get("livemode")), as_of)

    print(
        "sessions={counted_sessions} gross={gross_minor} refunded={refunded_minor} "
        "disputed={disputed_minor} net={raised_minor} {currency} sample={sample}".format(
            **config | totals
        )
    )

    if args.dry_run:
        return 0

    try:
        svg = render_progress.generate(config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        args.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {args.config} and {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
