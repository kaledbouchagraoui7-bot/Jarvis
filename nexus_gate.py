"""
nexus_gate.py — THE NEXUS: human-in-the-loop control gate.
==========================================================

Governance rule (Part C, non-negotiable):
    No Pillar 3a transfer, no IBKR order and no copy-trading action may
    execute without (a) passing the Swiss compliance guardrail AND
    (b) an explicit human approval recorded through this module.

Workflow
--------
1. A producer module (cashflow_bridge / ibkr_dca_engine / copytrade_mirror)
   calls `stage(kind, payload)` -> the action is persisted in
   `runtime/nexus_pending.json` with status "PENDING" and a unique id.
2. A human reviews and decides from the CLI:
       python nexus_gate.py list
       python nexus_gate.py show NX-...
       python nexus_gate.py approve NX-... --confirm I-AUTHORISE-THIS-ACTION
       python nexus_gate.py reject  NX-... --reason "..."
3. Execution modules call `require_approval(action_id)` immediately before
   any irreversible step; it raises NexusDenied unless status == APPROVED.
4. Every decision is appended to `runtime/nexus_history.json` (append-only
   audit trail) and mirrored into `runtime/investment_log.txt`, which the
   Auditing Agent reviews.

The gate is deliberately boring: plain JSON files, no network, no daemon —
a human must physically type the confirmation phrase on the machine.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

import config

CONFIRM_PHRASE = "I-AUTHORISE-THIS-ACTION"
VALID_KINDS = ("transfer_3a", "ibkr_order", "copytrade_rebalance", "spawn_worker")


class NexusDenied(RuntimeError):
    """Raised when an execution is attempted without a recorded approval."""


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [NEXUS] {msg}"
    print(line)
    with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _read(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _log(f"WARN: {path.name} unreadable/corrupt; treating as empty (fail-closed)")
        return []


def _write(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- producers --
def stage(kind: str, payload: dict, proposed_by: str = "system") -> str:
    """Register an action that requires human approval before execution."""
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown action kind {kind!r}; expected one of {VALID_KINDS}")
    pending = _read(config.NEXUS_PENDING)
    action_id = f"NX-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    pending.append({
        "id": action_id,
        "kind": kind,
        "payload": payload,
        "proposed_by": proposed_by,      # e.g. "Investment Analyst (propose-only)"
        "status": "PENDING",
        "staged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _write(config.NEXUS_PENDING, pending)
    _log(f"staged {action_id} kind={kind} by={proposed_by} awaiting human approval")
    return action_id


def status(action_id: str) -> str:
    for row in _read(config.NEXUS_PENDING):
        if row["id"] == action_id:
            return row["status"]
    return "UNKNOWN"


def require_approval(action_id: str) -> dict:
    """Fail-closed check used by every execution module right before acting."""
    for row in _read(config.NEXUS_PENDING):
        if row["id"] == action_id:
            if row["status"] == "APPROVED":
                return row
            raise NexusDenied(
                f"action {action_id} is {row['status']}, not APPROVED — "
                f"The Nexus gate blocks execution (human approval required).")
    raise NexusDenied(f"action {action_id} unknown to The Nexus — blocked.")


def list_pending() -> list:
    return [r for r in _read(config.NEXUS_PENDING) if r["status"] == "PENDING"]


def list_history(limit: int = 50) -> list:
    return _read(config.NEXUS_HISTORY)[-limit:]


# ------------------------------------------------------------------- human --
def decide(action_id: str, decision: str, reason: str = "") -> None:
    """Programmatic decision API (used by the CLI and the control-plane UI)."""
    pending = _read(config.NEXUS_PENDING)
    target = next((r for r in pending if r["id"] == action_id), None)
    if target is None:
        print(f"no such action: {action_id}")
        sys.exit(1)
    if target["status"] != "PENDING":
        print(f"action {action_id} already {target['status']} — decisions are final.")
        sys.exit(1)
    target["status"] = decision
    target["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    target["reason"] = reason
    _write(config.NEXUS_PENDING, pending)
    history = _read(config.NEXUS_HISTORY)
    history.append({"id": action_id, "kind": target["kind"], "decision": decision,
                    "reason": reason, "at": target["decided_at"],
                    "payload": target["payload"]})
    _write(config.NEXUS_HISTORY, history)
    _log(f"decision {decision} on {action_id}" + (f" reason={reason!r}" if reason else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The Nexus — human approval gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show pending + recent decisions")
    sh = sub.add_parser("show", help="full payload of one action")
    sh.add_argument("action_id")
    apv = sub.add_parser("approve", help="approve a pending action")
    apv.add_argument("action_id")
    apv.add_argument("--confirm", required=True,
                     help=f"type exactly: {CONFIRM_PHRASE}")
    rj = sub.add_parser("reject", help="reject a pending action")
    rj.add_argument("action_id")
    rj.add_argument("--reason", default="")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        pending = [r for r in _read(config.NEXUS_PENDING) if r["status"] == "PENDING"]
        if not pending:
            print("Nexus inbox empty — no action awaits approval.")
        for r in pending:
            p = r["payload"]
            amt = p.get("amount_chf", p.get("notional_chf", ""))
            print(f"{r['id']}  {r['kind']:<22} {r['proposed_by']:<34} "
                  f"{p.get('ticker', p.get('target', '')):<6} CHF {amt}")
        hist = _read(config.NEXUS_HISTORY)[-5:]
        if hist:
            print("\nlast decisions:")
            for h in hist:
                print(f"  {h['at']}  {h['id']}  -> {h['decision']}"
                      + (f" ({h['reason']})" if h.get("reason") else ""))
        return 0
    if args.cmd == "show":
        row = next((r for r in _read(config.NEXUS_PENDING) if r["id"] == args.action_id), None)
        print(json.dumps(row, indent=2) if row else f"no such action: {args.action_id}")
        return 0 if row else 1
    if args.cmd == "approve":
        if args.confirm != CONFIRM_PHRASE:
            print(f"confirmation phrase mismatch — expected exactly: {CONFIRM_PHRASE}")
            return 1
        decide(args.action_id, "APPROVED")
        return 0
    if args.cmd == "reject":
        decide(args.action_id, "REJECTED", args.reason)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
