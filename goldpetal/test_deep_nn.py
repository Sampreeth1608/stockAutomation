"""Tests for deep neural-net architectures (shallow/deep/deeper)."""

from __future__ import annotations

from deep_nn import ARCHS, compare_architectures, make_deep_mlp, train_architecture


def _synth_bars(n: int = 160) -> list[dict]:
    """Oscillating path so edge labels are mixed (wins + losses)."""
    bars = []
    px = 15000.0
    for i in range(n):
        # alternate short trends so MFE/MAE both occur
        wave = 12.0 if (i // 6) % 2 == 0 else -12.0
        px = px + wave * 0.35 + ((-1) ** i) * 0.4
        # book follows the wave so NET aligns with move
        if wave > 0:
            tbq, tsq = 1200.0 + i, 800.0
        else:
            tbq, tsq = 800.0, 1200.0 + i
        net = tbq - tsq
        bars.append(
            {
                "tf": "30m",
                "time": f"2026-08-{(i // 20) + 1:02d} {9 + (i % 10):02d}:{(i % 2) * 30:02d}:00",
                "open": px - 1.0,
                "high": px + abs(wave) * 0.2,
                "low": px - abs(wave) * 0.2,
                "close": px,
                "range_pts": abs(wave) * 0.4,
                "n_ticks": 10,
                "tbq_open": tbq - 5,
                "tbq_close": tbq,
                "tsq_open": tsq - 5,
                "tsq_close": tsq,
                "net": net,
                "net_delta": 5.0 if wave > 0 else -5.0,
                "imb_pct": abs(net) / max(tbq, tsq, 1) * 100.0,
                "price_delta": wave * 0.35,
                "ltq_sum": 20.0,
                "ltq_avg": 2.0,
                "buy5_sum": 50.0 if wave > 0 else 30.0,
                "sell5_sum": 30.0 if wave > 0 else 50.0,
                "depth_net": 20.0 if wave > 0 else -20.0,
                "depth_imb_pct": 25.0,
                "oi_close": 1000.0,
                "volume_close": 5000.0 + i * 10,
                "bar_volume": 40.0,
            }
        )
    return bars


def test_arch_sizes() -> None:
    assert ARCHS["shallow"] == (64, 32)
    assert len(ARCHS["deep"]) == 4
    assert len(ARCHS["deeper"]) == 4
    pipe = make_deep_mlp(hidden=ARCHS["deep"])
    assert "mlp" in pipe.named_steps


def test_train_deep_and_compare() -> None:
    bars = _synth_bars(160)
    # Loose TP/SL so both classes appear on synthetic waves
    bundle = train_architecture(
        bars, arch="deep", lags=2, horizon=5, tp_pts=5, sl_pts=5
    )
    assert bundle["arch"] == "deep"
    assert bundle["family"] == "deep_neural_net"
    assert "auc" in bundle["metrics"]

    report = compare_architectures(
        bars,
        arches=["shallow", "deep"],
        lags=2,
        horizon=5,
        tp_pts=5,
        sl_pts=5,
    )
    assert report["winner"] in {"shallow", "deep"}
    assert report["bundle"]["model"] is not None
    assert len(report["results"]) == 2


if __name__ == "__main__":
    test_arch_sizes()
    test_train_deep_and_compare()
    print("test_deep_nn: OK")
