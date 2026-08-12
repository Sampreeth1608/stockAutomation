"""Offline checks for updated net-pressure-only strategy."""

from __future__ import annotations

from strategy import BarSnapshot, PressureStrategy


def main() -> None:
    # Entry ignores priceΔ: only netΔ
    s = PressureStrategy()
    s.on_bar(BarSnapshot("t0", cmp=10000, bp=100, sp=100))  # net 0
    r_buy = s.on_bar(BarSnapshot("t1", cmp=9990, bp=200, sp=100))  # price down, netΔ=+100
    assert r_buy.action == "BUY", r_buy

    s2 = PressureStrategy()
    s2.on_bar(BarSnapshot("s0", cmp=10000, bp=100, sp=100))
    r_short = s2.on_bar(BarSnapshot("s1", cmp=10050, bp=50, sp=200))  # price up, netΔ=-150
    assert r_short.action == "SHORT", r_short

    # Long closes when netΔ decreases
    s3 = PressureStrategy()
    s3.on_bar(BarSnapshot("a0", cmp=10000, bp=100, sp=100))  # net0
    assert s3.on_bar(BarSnapshot("a1", cmp=10010, bp=200, sp=100)).action == "BUY"  # netΔ=100
    # prev_net=100; want netΔ=90 (<100) => net=190
    r_dec = s3.on_bar(BarSnapshot("a2", cmp=10020, bp=290, sp=100))
    assert r_dec.net_delta == 90
    assert r_dec.action == "CLOSE", r_dec

    # Short closes when netΔ increases (-1827 -> -376)
    s4 = PressureStrategy()
    s4.on_bar(BarSnapshot("b0", cmp=15000, bp=1000, sp=1000))
    assert s4.on_bar(BarSnapshot("b1", cmp=14900, bp=1000, sp=2827)).action == "SHORT"  # netΔ=-1827
    # -1827 -> -376 is an increase => CLOSE for short
    r_close_up = s4.on_bar(BarSnapshot("b2", cmp=14850, bp=1000, sp=3203))
    assert r_close_up.net_delta == -376
    assert r_close_up.action == "CLOSE", r_close_up

    # Short holds when netΔ decreases further (more negative)
    s4b = PressureStrategy()
    s4b.on_bar(BarSnapshot("e0", cmp=15000, bp=1000, sp=1000))
    assert s4b.on_bar(BarSnapshot("e1", cmp=14950, bp=1000, sp=1100)).action == "SHORT"  # netΔ=-100
    # prev_net=-100; netΔ=-300 => net=-400
    r_hold = s4b.on_bar(BarSnapshot("e2", cmp=14900, bp=1000, sp=1400))
    assert r_hold.net_delta == -300
    assert r_hold.action == "HOLD", r_hold

    # Exhaustion long: >=2% price up from entry and netΔ >= 3x entry netΔ
    s5 = PressureStrategy()
    s5.on_bar(BarSnapshot("c0", cmp=10000, bp=0, sp=0))
    assert s5.on_bar(BarSnapshot("c1", cmp=10000, bp=100, sp=0)).action == "BUY"  # entry netΔ=100
    # prev_net=100; netΔ=300 (>=3x100), price 10250 = +2.5%
    r_ex = s5.on_bar(BarSnapshot("c2", cmp=10250, bp=400, sp=0))
    assert r_ex.net_delta == 300
    assert r_ex.action == "CLOSE", r_ex
    assert "exhaustion" in r_ex.reason

    # Divergence: tiny price change, netΔ surged a lot
    s6 = PressureStrategy()
    s6.on_bar(BarSnapshot("d0", cmp=10000, bp=0, sp=0))
    assert s6.on_bar(BarSnapshot("d1", cmp=10000, bp=50, sp=0)).action == "BUY"  # netΔ=50
    # price almost flat (0.05%), netΔ = 120 (>= 2x 50)
    # prev_net=50; netΔ=120 => net=170
    r_div = s6.on_bar(BarSnapshot("d2", cmp=10005, bp=170, sp=0))
    assert r_div.action == "CLOSE", r_div
    assert "divergence" in r_div.reason

    print("strategy checks passed")


if __name__ == "__main__":
    main()
