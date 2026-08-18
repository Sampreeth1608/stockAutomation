#!/usr/bin/env python3
"""Learn which OHLC / wick / volume / prev-bar / higher-TF relations predict the next close.

Research only. Does not paper or change S13/S16.

Each mix of ohlc / wick / prev / vol / htf is its own named strategy
(CR_OHLC, CR_OHLC_VOL, … 31 books). All stay off. Decide later which to paper.

  ./venv/bin/python learn_candle_relations.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python learn_candle_relations.py --db data/ticks.db --tf 1h --higher 1d
  ./venv/bin/python learn_candle_relations.py --db data/ticks.db --tf 30m --higher 1h

Train on earlier bars, test on later bars (time split). Label = next bar close
up vs down. Volume is the bar delta of Angel session-cumulative
volume_trade_for_the_day. Report top relations, OOS AUC, and a fee-aware
one-bar paper sim. Nothing is enabled until you pick a row and we wire a
separate paper book.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from backtest_hhhl_candles import in_session, make_charge_cfg
from candle_rel_books import CANDLE_REL_BOOKS
from candle_relations import (
    FEATURE_COLUMNS,
    RelBar,
    build_rel_bars,
    columns_for_groups,
    labeled_rows,
    load_tick_rows,
)
from charges import apply_charges_and_tax

TF_MINUTES = {"30m": 30, "1h": 60, "1d": 1440}
FEATURE_COMBOS = tuple((b.name, b.groups) for b in CANDLE_REL_BOOKS)


def _matrix(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...] | list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    cols = list(columns) if columns is not None else list(FEATURE_COLUMNS)
    x = np.array(
        [[float(r["feat"].get(name) or 0.0) for name in cols] for r in rows],
        dtype=float,
    )
    y = np.array([int(r["y_up"]) for r in rows], dtype=int)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return x, y


def _corr_rank(
    x: np.ndarray,
    y: np.ndarray,
    columns: tuple[str, ...] | list[str] | None = None,
    k: int = 8,
) -> list[dict[str, Any]]:
    cols = list(columns) if columns is not None else list(FEATURE_COLUMNS)
    if len(y) < 4:
        return []
    yc = y.astype(float) - float(y.mean())
    out: list[dict[str, Any]] = []
    for i, name in enumerate(cols):
        if i >= x.shape[1]:
            break
        col = x[:, i]
        if float(np.std(col)) < 1e-12:
            continue
        xc = col - float(col.mean())
        denom = float(np.sqrt((xc * xc).sum() * (yc * yc).sum()))
        if denom <= 1e-12:
            continue
        rho = float((xc * yc).sum() / denom)
        out.append({"feature": name, "corr": round(rho, 4)})
    out.sort(key=lambda r: abs(float(r["corr"])), reverse=True)
    return out[:k]


def _fit(x: np.ndarray, y: np.ndarray) -> Any:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    clf = Pipeline(
        [
            ("sc", StandardScaler()),
            (
                "lr",
                LogisticRegression(
                    max_iter=400,
                    class_weight="balanced",
                    solver="lbfgs",
                ),
            ),
        ]
    )
    clf.fit(x, y)
    return clf


def _auc(clf: Any, x: np.ndarray, y: np.ndarray) -> float | None:
    if len(set(y.tolist())) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    p = clf.predict_proba(x)[:, 1]
    return float(roc_auc_score(y, p))


def _top_coef(
    clf: Any,
    columns: tuple[str, ...] | list[str] | None = None,
    k: int = 8,
) -> list[dict[str, Any]]:
    cols = list(columns) if columns is not None else list(FEATURE_COLUMNS)
    lr = clf.named_steps["lr"]
    coef = np.asarray(lr.coef_).ravel()
    rows = [
        {"feature": cols[i], "weight": round(float(coef[i]), 4)}
        for i in range(min(len(cols), len(coef)))
    ]
    rows.sort(key=lambda r: abs(float(r["weight"])), reverse=True)
    return rows[:k]


def _fmt_auc(v: Any) -> str:
    if v is None:
        return "   -  "
    return f"{float(v):6.3f}"


def _eval_split(
    train: list[dict[str, Any]],
    test: list[dict[str, Any]],
    columns: tuple[str, ...],
    *,
    lots: float,
    fees: bool,
    long_p: float,
    short_p: float,
) -> dict[str, Any]:
    x_tr, y_tr = _matrix(train, columns)
    x_te, y_te = _matrix(test, columns)
    out: dict[str, Any] = {
        "n_feat": len(columns),
        "paper": False,
        "train_corr": _corr_rank(x_tr, y_tr, columns),
    }
    try:
        clf = _fit(x_tr, y_tr)
    except Exception as exc:
        out["error"] = f"fit_failed:{type(exc).__name__}"
        return out
    out["train_auc"] = _auc(clf, x_tr, y_tr)
    out["test_auc"] = _auc(clf, x_te, y_te)
    out["top_weights"] = _top_coef(clf, columns)
    sc = clf.named_steps["sc"]
    lr = clf.named_steps["lr"]
    out["model"] = {
        "columns": list(columns),
        "scaler_mean": [float(x) for x in sc.mean_],
        "scaler_scale": [float(x) for x in sc.scale_],
        "coef": [float(x) for x in np.asarray(lr.coef_).ravel()],
        "intercept": float(np.asarray(lr.intercept_).ravel()[0]),
        "long_p": long_p,
        "short_p": short_p,
    }
    p_te = clf.predict_proba(x_te)[:, 1]
    out["oos_one_bar"] = _one_bar_sim(
        test, p_te, lots=lots, fees=fees, long_p=long_p, short_p=short_p
    )
    return out


def _one_bar_sim(
    rows: list[dict[str, Any]],
    proba: np.ndarray,
    *,
    lots: float,
    fees: bool,
    long_p: float,
    short_p: float,
) -> dict[str, Any]:
    cfg = make_charge_cfg(fees=fees, lots=lots)
    n = 0
    wins = 0
    after = 0.0
    gross = 0.0
    for row, p in zip(rows, proba, strict=True):
        if p >= long_p:
            side = "BUY"
            pts = float(row["next_pts"])
        elif p <= short_p:
            side = "SELL"
            pts = -float(row["next_pts"])
        else:
            continue
        settled = apply_charges_and_tax(
            pts,
            cfg,
            side=side,
            entry_price=float(row["close"]),
            exit_price=float(row["next_close"]),
        )
        n += 1
        gross += float(settled["gross_pnl"])
        after += float(settled["pnl_after_tax"])
        if float(settled["pnl_after_tax"]) > 0:
            wins += 1
    return {
        "n_trades": n,
        "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
        "gross_inr": round(gross, 1),
        "after_tax_inr": round(after, 1),
        "long_p": long_p,
        "short_p": short_p,
    }


def run_tf(
    rows_ltp: list[tuple],
    *,
    tf: str,
    higher: str | None,
    lots: float,
    fees: bool,
    session: bool,
    train_frac: float,
    long_p: float,
    short_p: float,
) -> dict[str, Any]:
    minutes = TF_MINUTES[tf]
    candles = build_rel_bars(rows_ltp, minutes)
    h_bars: list[RelBar] = []
    h_min = 1440
    if higher:
        h_min = TF_MINUTES[higher]
        h_bars = build_rel_bars(rows_ltp, h_min)
    sess = None
    if session and tf != "1d":
        sess = lambda c: in_session(c)
    labeled = labeled_rows(
        candles, higher=h_bars, higher_minutes=h_min, session_ok=sess
    )
    summary: dict[str, Any] = {
        "tf": tf,
        "higher": higher or "-",
        "bars": len(candles),
        "rows": len(labeled),
        "formula": (
            "31 CR_* strategies (every ohlc/wick/prev/vol/htf mix); "
            "all off; label = next close up/down"
        ),
        "paper": False,
        "note": "research only — CR_* books are not paper. decide next.",
    }
    if len(labeled) < 12:
        summary["error"] = "not enough labeled bars"
        return summary
    cut = max(1, int(len(labeled) * train_frac))
    if cut >= len(labeled):
        cut = len(labeled) - 1
    train, test = labeled[:cut], labeled[cut:]
    summary["train_n"] = int(len(train))
    summary["test_n"] = int(len(test))
    all_cols = tuple(FEATURE_COLUMNS)
    all_fit = _eval_split(
        train,
        test,
        all_cols,
        lots=lots,
        fees=fees,
        long_p=long_p,
        short_p=short_p,
    )
    summary.update(
        {
            "n_feat": all_fit.get("n_feat"),
            "train_corr": all_fit.get("train_corr"),
            "train_auc": all_fit.get("train_auc"),
            "test_auc": all_fit.get("test_auc"),
            "top_weights": all_fit.get("top_weights"),
            "oos_one_bar": all_fit.get("oos_one_bar"),
        }
    )
    if all_fit.get("error"):
        summary["error"] = all_fit["error"]
    combos: list[dict[str, Any]] = []
    for book in CANDLE_REL_BOOKS:
        if higher is None and "htf" in book.groups:
            continue
        rec = _eval_split(
            train,
            test,
            columns_for_groups(book.groups),
            lots=lots,
            fees=fees,
            long_p=long_p,
            short_p=short_p,
        )
        rec["combo"] = book.name
        rec["strategy"] = book.name
        rec["groups"] = list(book.groups)
        rec["paper"] = False
        rec["enabled"] = False
        rec["formula"] = book.formula
        combos.append(rec)
    summary["strategies"] = combos
    summary["combos"] = combos
    summary["n_candidates"] = len(combos)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--tf", default="1h,1d")
    ap.add_argument("--higher", default="1d,")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--long-p", type=float, default=0.55)
    ap.add_argument("--short-p", type=float, default=0.45)
    ap.add_argument("--out-dir", default="data/learn/candle_relations")
    args = ap.parse_args()

    tfs = [t.strip() for t in str(args.tf).split(",") if t.strip()]
    highers = [h.strip() for h in str(args.higher).split(",")]
    while len(highers) < len(tfs):
        highers.append("")
    db = Path(args.db)
    rows = load_tick_rows(db)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    print(
        f"Candle-relation strategies ({len(CANDLE_REL_BOOKS)} CR_* books, all OFF). "
        "Not paper. Decide next."
    )
    print(f"ticks={len(rows)} db={db}")
    for tf, htf in zip(tfs, highers, strict=False):
        if tf not in TF_MINUTES:
            print(f"skip unknown tf {tf}")
            continue
        higher = htf if htf in TF_MINUTES else None
        rep = run_tf(
            rows,
            tf=tf,
            higher=higher,
            lots=float(args.lots),
            fees=bool(args.fees),
            session=bool(args.session),
            train_frac=float(args.train_frac),
            long_p=float(args.long_p),
            short_p=float(args.short_p),
        )
        reports.append(rep)
        print(
            f"  {tf} higher={higher or '-'} rows={rep.get('rows')} "
            f"all train_auc={rep.get('train_auc')} test_auc={rep.get('test_auc')} "
            f"candidates={rep.get('n_candidates')} oos={rep.get('oos_one_bar')}"
        )
        if rep.get("train_corr"):
            top = ", ".join(
                f"{r['feature']}={r['corr']}" for r in rep["train_corr"][:8]
            )
            print(f"    all-corr {top}")
        combos = list(rep.get("strategies") or rep.get("combos") or [])
        if combos:
            print(
                f"    {len(combos)} separate strategies, all OFF. "
                "sorted by test AUC — decide next"
            )
            print(
                "    strategy                         feats  train   test  oos_n   win%    after₹"
            )
            ranked = sorted(
                combos,
                key=lambda r: (
                    r.get("test_auc") is None,
                    -(float(r["test_auc"]) if r.get("test_auc") is not None else 0.0),
                ),
            )
            for c in ranked:
                oos = c.get("oos_one_bar") or {}
                print(
                    f"    {str(c.get('strategy') or c.get('combo')):<32} "
                    f"{int(c.get('n_feat') or 0):5d}  "
                    f"{_fmt_auc(c.get('train_auc'))} {_fmt_auc(c.get('test_auc'))}  "
                    f"{int(oos.get('n_trades') or 0):5d}  "
                    f"{float(oos.get('win_rate') or 0):5.1f}  "
                    f"{float(oos.get('after_tax_inr') or 0):8.1f}"
                )
    catalog = out_dir / "strategies.json"
    catalog.write_text(
        json.dumps(
            {
                "paper": False,
                "note": "31 CR_* strategies, all off. Decide next. Not S13/S16.",
                "books": [b.as_row() for b in CANDLE_REL_BOOKS],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"wrote {catalog}")
    print(f"wrote {latest}")


if __name__ == "__main__":
    main()
