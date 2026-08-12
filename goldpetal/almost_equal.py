"""Almost-equal helpers for state signs (+ / - / =).

Qty / volume: relative band (default 5%, sensible range 3–9%).
Price / high / low: separate tighter band so '=' is not hundreds of points
on a ₹10k instrument (default 0.05% ≈ 5 pts, or absolute pts override).
"""

from __future__ import annotations


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def sign_rel(curr: float, prev: float, equal_pct: float) -> str:
    """Compare curr vs prev with relative almost-equal band.

    equal_pct=0.05 means |Δ|/|prev| <= 5% → '='.
    """
    prev_abs = abs(float(prev))
    if prev_abs < 1e-12:
        d = float(curr) - float(prev)
        if abs(d) < 1e-12:
            return "="
        return "+" if d > 0 else "-"
    rel = (float(curr) - float(prev)) / prev_abs
    band = abs(float(equal_pct))
    if abs(rel) <= band:
        return "="
    return "+" if rel > 0 else "-"


def sign_px(
    curr: float,
    prev: float,
    *,
    equal_px_pct: float = 0.0005,
    equal_pts: float | None = None,
) -> str:
    """Price/high/low compare.

    Prefer absolute points if equal_pts set; else relative equal_px_pct
    (0.0005 = 0.05% ≈ 5 pts at 10_000).
    """
    d = float(curr) - float(prev)
    if equal_pts is not None:
        if abs(d) <= abs(equal_pts):
            return "="
        return "+" if d > 0 else "-"
    prev_abs = max(abs(float(prev)), 1e-9)
    if abs(d) / prev_abs <= abs(equal_px_pct):
        return "="
    return "+" if d > 0 else "-"


def triple_state(
    a_c: float,
    a_p: float,
    b_c: float,
    b_p: float,
    *,
    a_is_px: bool,
    b_is_px: bool,
    equal_pct: float,
    equal_px_pct: float,
    equal_pts: float | None,
    prefix_a: str,
    prefix_b: str,
) -> str:
    sa = (
        sign_px(a_c, a_p, equal_px_pct=equal_px_pct, equal_pts=equal_pts)
        if a_is_px
        else sign_rel(a_c, a_p, equal_pct)
    )
    sb = (
        sign_px(b_c, b_p, equal_px_pct=equal_px_pct, equal_pts=equal_pts)
        if b_is_px
        else sign_rel(b_c, b_p, equal_pct)
    )
    return f"{prefix_a}{sa}_{prefix_b}{sb}"
