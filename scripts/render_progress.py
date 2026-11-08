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
    sample        bool; true renders a visible "Sample preview" label

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


def _pct_label(pct: float) -> str:
    """Percent text that stays meaningful for tiny totals."""
    if pct == 0:
        return "0.0%"
    if pct < 1:
        return f"{pct:.2f}%"
    return f"{pct:.1f}%"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def generate(data: object) -> str:
    d = validate(data)
    raised, goal = d["raised_minor"], d["goal_minor"]
    pct = 100 * raised / goal
    bar_pct = min(pct, 100)
    width = round(696 * bar_pct / 100, 2)
    if raised > 0 and width < 8:
        # Keep the first small donation visible instead of a sub-pixel sliver.
        width = 8.0
    radius = min(7, width / 2) if width else 0
    pct_label = _pct_label(pct)
    width_attr = f"{width:.2f}".rstrip("0").rstrip(".")
    radius_attr = f"{radius:.2f}".rstrip("0").rstrip(".")

    title = html.escape(_clip(d["campaign"], 52), quote=True)
    as_of = html.escape(d["as_of"], quote=True)
    if d["sample"]:
        state = "Sample preview — live totals update automatically"
    else:
        state = f"As of {as_of}"
    state = html.escape(state, quote=True)
    aria = html.escape(
        f"{d['campaign']}: {pct_label.rstrip('%')} percent of {_money(goal, d['currency'])} goal funded",
        quote=True,
    )
    raised_text = html.escape(_money(raised, d["currency"]))
    goal_text = html.escape(_money(goal, d["currency"]))
    pill_text = f"{pct_label} funded"
    pill_w = max(78, round(len(pill_text) * 7.4 + 28))
    pill_x = 728 - pill_w
    pill_cx = pill_x + pill_w / 2

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="760" height="208" viewBox="0 0 760 208" role="img" aria-label="{aria}">
  <title>{title} — fundraising progress</title>
  <defs>
    <linearGradient id="card" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#f6f9fd"/>
    </linearGradient>
    <linearGradient id="fill" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#4ade80"/>
      <stop offset="1" stop-color="#16a34a"/>
    </linearGradient>
  </defs>
  <rect x="0.5" y="0.5" width="759" height="207" rx="20" fill="url(#card)" stroke="#e5eaf1"/>
  <text x="32" y="40" font-size="11" font-family="Arial, sans-serif" font-weight="700" letter-spacing="1.6" fill="#94a3b8">FUNDRAISING PROGRESS</text>
  <text x="32" y="72" font-size="20" font-family="Arial, sans-serif" font-weight="700" fill="#0f172a">{title}</text>
  <rect x="{pill_x}" y="90" width="{pill_w}" height="30" rx="15" fill="#ecfdf5" stroke="#bbf7d0"/>
  <text x="{pill_cx}" y="110" text-anchor="middle" font-size="13.5" font-family="Arial, sans-serif" font-weight="800" fill="#15803d">{pill_text}</text>
  <text x="32" y="118" font-size="32" font-family="Arial, sans-serif" font-weight="800" fill="#0f172a">{raised_text}<tspan font-size="15" font-weight="600" fill="#64748b"> of {goal_text}</tspan></text>
  <rect x="32" y="142" width="696" height="16" rx="8" fill="#eef2f7" stroke="#e6ebf2"/>
  <rect x="32" y="142" width="{width_attr}" height="16" rx="{radius_attr}" fill="url(#fill)"/>
  <rect x="32" y="143" width="{width_attr}" height="6" rx="{radius_attr}" fill="#ffffff" opacity="0.18"/>
  <text x="32" y="192" font-size="12" font-family="Arial, sans-serif" fill="#94a3b8">{state}</text>
  <text x="728" y="192" text-anchor="end" font-size="12" font-family="Arial, sans-serif" fill="#cbd5e1">Updated hourly from Stripe</text>
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
