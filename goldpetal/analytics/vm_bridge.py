"""Bridge from Mac analytics desk → GCP trading VM (source of truth for writes).

UI runs on Mac; decisions that change the bot are applied on the VM via
`gcloud compute ssh`, then the local snapshot is re-synced.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VmConfig:
    vm: str = "sampreeth-love-story"
    zone: str = "asia-south1-c"
    remote_dir: str = "/home/sampreeth1608/goldpetal"

    @classmethod
    def from_env(cls) -> "VmConfig":
        return cls(
            vm=os.getenv("GP_VM", "sampreeth-love-story"),
            zone=os.getenv("GP_ZONE", "asia-south1-c"),
            remote_dir=os.getenv("GP_REMOTE_DIR", "/home/sampreeth1608/goldpetal"),
        )


def _run(cmd: list[str], *, timeout: int = 120) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def ssh_python(cfg: VmConfig, py_source: str, *, timeout: int = 180) -> tuple[int, str, str]:
    """Run a short Python snippet inside the VM goldpetal venv."""
    remote = (
        f"cd {cfg.remote_dir} && "
        f"source venv/bin/activate && "
        f"python3 - <<'PY'\n{py_source}\nPY"
    )
    cmd = [
        "gcloud",
        "compute",
        "ssh",
        cfg.vm,
        f"--zone={cfg.zone}",
        "--command",
        remote,
    ]
    return _run(cmd, timeout=timeout)


def decide_proposal_remote(
    proposal_id: str,
    decision: str,
    note: str = "",
    cfg: VmConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or VmConfig.from_env()
    note_lit = json.dumps(note)
    py = f"""
from proposals import decide_proposal, get_proposal
import json
p = decide_proposal({proposal_id!r}, {decision!r}, note={note_lit})
print(json.dumps({{
  "id": p.id,
  "strategy": p.strategy,
  "status": p.status,
  "safety_ok": p.safety_ok,
  "env_patch": p.env_patch,
  "title": p.title,
}}))
"""
    code, out, err = ssh_python(cfg, py)
    if code != 0:
        return {"ok": False, "error": err or out, "code": code}
    line = out.strip().splitlines()[-1] if out.strip() else "{}"
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": out or err, "code": code}
    payload["ok"] = True
    return payload


def set_control_remote(
    *,
    emergency_off: bool | None = None,
    trading_enabled: bool | None = None,
    live_unlocked: bool | None = None,
    cfg: VmConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or VmConfig.from_env()
    bits = []
    if emergency_off is not None:
        bits.append(f"st = set_emergency({bool(emergency_off)})")
    if trading_enabled is not None:
        bits.append(f"st = set_trading_enabled({bool(trading_enabled)})")
    if live_unlocked is not None:
        bits.append(f"st = set_live_unlocked({bool(live_unlocked)})")
    if not bits:
        return {"ok": False, "error": "nothing to change"}
    py = (
        "from control_state import set_emergency, set_trading_enabled, set_live_unlocked, load_state\n"
        + "\n".join(bits)
        + "\nimport json\nprint(json.dumps(load_state().to_dict()))\n"
    )
    code, out, err = ssh_python(cfg, py)
    if code != 0:
        return {"ok": False, "error": err or out, "code": code}
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": out or err}
    payload["ok"] = True
    return payload


def save_capital_remote(payload: dict[str, Any], cfg: VmConfig | None = None) -> dict[str, Any]:
    cfg = cfg or VmConfig.from_env()
    blob = json.dumps(payload)
    py = f"""
import json
from capital import load_capital, save_capital, capital_snapshot, update_strategy_budget
data = json.loads({blob!r})
plan = load_capital()
if "total_capital_inr" in data:
    plan.total_capital_inr = float(data["total_capital_inr"])
if "cash_reserve_pct" in data:
    plan.cash_reserve_pct = float(data["cash_reserve_pct"])
if "day_loss_limit_inr" in data:
    plan.day_loss_limit_inr = float(data["day_loss_limit_inr"])
if "max_lots_total" in data:
    plan.max_lots_total = int(data["max_lots_total"])
save_capital(plan)
for row in data.get("strategies") or []:
    update_strategy_budget(
        row["strategy"],
        budget_inr=float(row.get("budget_inr", 0)),
        max_lots=int(row.get("max_lots", 1)),
        max_open_trades=int(row.get("max_open_trades", 1)),
        enabled=bool(row.get("enabled", True)),
    )
print(json.dumps({{"ok": True, "capital": capital_snapshot()}}))
"""
    code, out, err = ssh_python(cfg, py, timeout=120)
    if code != 0:
        return {"ok": False, "error": err or out, "code": code}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": out or err}


def sync_snapshot(data_dir: Path, cfg: VmConfig | None = None, *, skip_db: bool = False) -> tuple[int, str]:
    """Call the shell sync script."""
    cfg = cfg or VmConfig.from_env()
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "sync_analytics_mac.sh"
    env = os.environ.copy()
    env["VM"] = cfg.vm
    env["ZONE"] = cfg.zone
    env["REMOTE_DIR"] = cfg.remote_dir
    env["ANALYTICS_DIR"] = str(data_dir)
    args = [str(script)]
    if skip_db:
        args.append("--skip-db")
    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=600)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
