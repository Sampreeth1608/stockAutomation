"""Quick offline checks for pressure strategy rules."""

from __future__ import annotations

from strategy import BarSnapshot, PressureStrategy


def main() -> None:
    s = PressureStrategy()

    # Baseline
    r0 = s.on_bar(BarSnapshot("t0", cmp=14340, bp=3759, sp=4565))
    assert r0.action == "WAIT", r0

    # priceΔ=+3, net: -806 -> -1182 => netΔ=-376; flat, signs mismatch => FLAT
    r1 = s.on_bar(BarSnapshot("t1", cmp=14343, bp=4308, sp=5490))
    assert r1.price_delta == 3
    assert r1.net_delta == -376
    assert r1.action == "FLAT", r1

    # Force a long setup: rising price + rising net
    s2 = PressureStrategy()
    s2.on_bar(BarSnapshot("a0", cmp=100, bp=50, sp=40))  # net=10
    r_buy = s2.on_bar(BarSnapshot("a1", cmp=110, bp=80, sp=40))  # priceΔ=10, netΔ=30
    assert r_buy.action == "BUY", r_buy

    # Hold while aligned and netΔ increases: prev netΔ=30, now net=50-> netΔ from prev net 40 = 10? 
    # prev_cmp=110, prev_net=40; new cmp=120 bp=100 sp=40 net=60; priceΔ=10 netΔ=20; 20<30 decrease => CLOSE
    r_close_dec = s2.on_bar(BarSnapshot("a2", cmp=120, bp=100, sp=40))
    assert r_close_dec.action == "CLOSE", r_close_dec

    # Decrease example from sheet: 269 -> 266 while long
    s3 = PressureStrategy()
    s3.on_bar(BarSnapshot("b0", cmp=14340, bp=1000, sp=1000))  # net 0
    # Make netΔ=269 and enter long: need price up and net up
    r_b1 = s3.on_bar(BarSnapshot("b1", cmp=14363, bp=1269, sp=1000))  # priceΔ>0 netΔ=269
    assert r_b1.action == "BUY", r_b1
    # Next: price still up, netΔ=266 (<269) => CLOSE
    # prev_net = 269; want net_delta=266 => net=269+266=535 => bp-sp=535
    r_b2 = s3.on_bar(BarSnapshot("b2", cmp=14401, bp=1535, sp=1000))
    assert r_b2.net_delta == 266
    assert r_b2.action == "CLOSE", r_b2

    # Negative increase hold: -1827 -> -376 while short
    s4 = PressureStrategy()
    s4.on_bar(BarSnapshot("c0", cmp=15000, bp=1000, sp=1000))  # net 0
    r_short = s4.on_bar(BarSnapshot("c1", cmp=14900, bp=1000, sp=2827))  # priceΔ=-100 netΔ=-1827
    assert r_short.action == "SHORT", r_short
    # netΔ=-376 (> -1827) and price still down, net still negative => HOLD
    # prev_net=-1827; netΔ=-376 => net=-1827-376=-2203
    r_hold = s4.on_bar(BarSnapshot("c2", cmp=14850, bp=1000, sp=3203))
    assert r_hold.net_delta == -376
    assert r_hold.action == "HOLD", r_hold

    print("strategy checks passed")


if __name__ == "__main__":
    main()
