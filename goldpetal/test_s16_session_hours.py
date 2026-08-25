"""S16 night-stop / morning-start helpers. No GCP calls."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_boot_starts_supervise_and_local_desk() -> None:
    boot = (ROOT / "scripts" / "boot_s16_session.sh").read_text(encoding="utf-8")
    assert "supervise.sh" in boot
    assert "run_desk_vm.sh" in boot
    assert "0.0.0.0" not in boot
    assert "DRY_RUN=false" not in boot
    inst = (ROOT / "scripts" / "install_s16_boot.sh").read_text(encoding="utf-8")
    assert "@reboot" in inst
    assert "boot_s16_session.sh" in inst


def test_gcp_schedule_is_weekday_ist() -> None:
    gcp = (ROOT / "scripts" / "gcp_s16_session_hours.sh").read_text(encoding="utf-8")
    assert "Asia/Kolkata" in gcp
    assert "40 8 * * 1-5" in gcp
    assert "55 23 * * 1-5" in gcp
    assert "create instance-schedule" in gcp
    assert "create-instance-schedule" not in gcp
    assert "Do not delete the boot disk" in gcp
    assert "e2-small" in gcp
    assert "e2-medium" in gcp
    assert "--apply" in gcp
    assert "gcp_s16_lower_size.sh" in gcp


def test_gcp_lower_size_is_e2_small_and_keeps_boot_disk() -> None:
    lower = (ROOT / "scripts" / "gcp_s16_lower_size.sh").read_text(encoding="utf-8")
    assert "e2-small" in lower
    assert "e2-micro" in lower
    assert "Do not use e2-micro / 1 GB" in lower
    assert "set-machine-type" in lower
    assert "cannot shrink a boot disk" in lower
    assert "Do not delete the boot disk" in lower
    assert "--apply-ram" in lower
    assert "Do not Arm live" in lower


if __name__ == "__main__":
    test_boot_starts_supervise_and_local_desk()
    test_gcp_schedule_is_weekday_ist()
    test_gcp_lower_size_is_e2_small_and_keeps_boot_disk()
    print("ALL test_s16_session_hours OK")
