#!/usr/bin/env python3
"""Render a public, static fundraising progress SVG from *verified* aggregate JSON.

Offline only: no Stripe access, no network, no third-party dependencies.
Never place API keys, webhook secrets, donor names/emails, or raw payment
payloads in the input file: the rendered SVG is meant to be published.

Input contract (see assets/progress.schema.json):
    campaign      str, 1..80 chars
    currency      ISO code present in CURRENCIES
    raised_minor  int >= 0, in the currency's minor unit
    goal_minor    int > 0, in the currency's minor unit
    as_of         str, 1..30 chars (ISO date recommended)
    sample        bool; true renders a visible "SAMPLE DATA - NOT LIVE" label

Exit codes: 0 success, 2 invalid input or I/O error.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

# currency -> (prefix, suffix, minor-unit decimal places)
CURRENCIES = {
    "CAD": ("$", "", 2),
    "USD": ("$", "", 2),
    "AUD": ("$", "", 2),
    "NZD": ("$", "", 2),
    "EUR": ("€", "", 2),
    "GBP": ("£", "", 2),
    "CHF": ("CHF ", "", 2),
    "SEK": ("", " kr", 2),
    "NOK": ("", " kr", 2),
    "DKK": ("", " kr", 2),
    "PLN": ("", " zł", 2),
    "INR": ("₹", "", 2),
    "JPY": ("¥", "", 0),
    "KRW": ("₩", "", 0),
}

REQUIRED = {"campaign", "currency", "raised_minor", "goal_minor", "as_of", "sample"}
MAX_AMOUNT = 10**15  # sanity bound on minor units


def _text(data: dict, key: str, limit: int) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if not value:
        raise ValueError(f"{key} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{key} must be <= {limit} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{key} must not contain control characters")
    return value


def _amount(data: dict, key: str) -> int:
    value = data[key]
    # bool is a subclass of int; reject it explicitly.
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer number of minor units")
    if value < 0:
        raise ValueError(f"{key} must be >= 0")
    if value > MAX_AMOUNT:
        raise ValueError(f"{key} is unreasonably large")
    return value


def validate(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    missing = sorted(REQUIRED - set(data))
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
    unknown = sorted(set(data) - REQUIRED)
    if unknown:
        raise ValueError("unexpected fields: " + ", ".join(unknown))

    currency = data["currency"]
    if not isinstance(currency, str) or currency not in CURRENCIES:
        raise ValueError(
            "currency must be one of: " + ", ".join(sorted(CURRENCIES))
        )
    if type(data["sample"]) is not bool:
        raise ValueError("sample must be a boolean")

    raised = _amount(data, "raised_minor")
    goal = _amount(data, "goal_minor")
    if goal <= 0:
        raise ValueError("goal_minor must be > 0")

    return {
        "campaign": _text(data, "campaign", 80),
        "currency": currency,
        "raised_minor": raised,
        "goal_minor": goal,
        "as_of": _text(data, "as_of", 30),
        "sample": data["sample"],
    }


def _money(minor: int, currency: str) -> str:
    prefix, suffix, decimals = CURRENCIES[currency]
    major = minor / (10**decimals)
    return f"{prefix}{major:,.{decimals}f}{suffix} {currency}"


def generate(data: object) -> str:
    d = validate(data)
    raised, goal = d["raised_minor"], d["goal_minor"]
    pct = 100 * raised / goal
    bar_pct = min(pct, 100)
    width = round(700 * bar_pct / 100, 2)
    radius = min(10, width / 2) if width else 0
    width_attr = f"{width:.2f}".rstrip("0").rstrip(".")
    radius_attr = f"{radius:.2f}".rstrip("0").rstrip(".")

    title = html.escape(d["campaign"], quote=True)
    as_of = html.escape(d["as_of"], quote=True)
    if d["sample"]:
        state = f"SAMPLE DATA — NOT LIVE · as of {as_of}"
    else:
        state = f"As of {as_of}"
    state = html.escape(state, quote=True)
    aria = html.escape(
        f"{d['campaign']}: {pct:.1f} percent of {_money(goal, d['currency'])} goal funded",
        quote=True,
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="760" height="168" viewBox="0 0 760 168" role="img" aria-label="{aria}">
  <title>{title} — fundraising progress</title>
  <rect x="0" y="0" width="760" height="168" rx="16" fill="#f8fafc"/>
  <text x="30" y="37" font-size="21" font-family="Arial, sans-serif" font-weight="bold" fill="#172033">{title}</text>
  <text x="30" y="78" font-size="25" font-family="Arial, sans-serif" font-weight="bold" fill="#14202e">{html.escape(_money(raised, d["currency"]))} / {html.escape(_money(goal, d["currency"]))}</text>
  <text x="730" y="78" text-anchor="end" font-size="20" font-family="Arial, sans-serif" fill="#334155">{pct:.1f}%</text>
  <rect x="30" y="95" width="700" height="20" rx="10" fill="#e2e8f0"/>
  <rect x="30" y="95" width="{width_attr}" height="20" rx="{radius_attr}" fill="#16a34a"/>
  <text x="30" y="145" font-size="15" font-family="Arial, sans-serif" fill="#475569">{state}</text>
</svg>
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a sanitized, static donation-progress SVG. No network access."
    )
    parser.add_argument("--input", required=True, type=Path, help="aggregate JSON file")
    parser.add_argument("--output", required=True, type=Path, help="SVG destination")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the input and print the SVG to stdout without writing a file",
    )
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: {args.input} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        svg = generate(data)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        sys.stdout.write(svg)
        return 0

    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write {args.output}: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
