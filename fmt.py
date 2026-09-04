"""Number formatting helpers — Indian style (₹1,25,000 / ₹12.5L / ₹1.2Cr)."""
from __future__ import annotations
import math


def _num(n) -> float | None:
    try:
        if n is None:
            return None
        f = float(n)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def inr(n) -> str:
    """Full Indian grouping, no decimals: 125000 -> '₹1,25,000'."""
    f = _num(n)
    if f is None:
        return '₹0'
    sign = '-' if f < 0 else ''
    s = str(int(round(abs(f))))
    if len(s) <= 3:
        return f'{sign}₹{s}'
    last3, rest = s[-3:], s[:-3]
    groups = []
    while len(rest) > 2:
        groups.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.append(rest)
    return f"{sign}₹{','.join(reversed(groups))},{last3}"


def inr_short(n) -> str:
    """Compact: 125000 -> '₹1.25L', 12500000 -> '₹1.25Cr', 850 -> '₹850'."""
    f = _num(n)
    if f is None:
        return '₹0'
    sign = '-' if f < 0 else ''
    a = abs(f)
    if a >= 1e7:
        return f'{sign}₹{a/1e7:.2f}Cr'
    if a >= 1e5:
        return f'{sign}₹{a/1e5:.2f}L'
    if a >= 1e3:
        return f'{sign}₹{a/1e3:.1f}K'
    return f'{sign}₹{a:.0f}'


def units(n) -> str:
    f = _num(n)
    if f is None:
        return '0'
    return inr(f).replace('₹', '')


def pct(n, digits: int = 1) -> str:
    f = _num(n)
    if f is None:
        return '–'
    return f'{f:.{digits}f}%'
