"""
automaton.py — THE CENTRAL AUTOMATON (self-replicating, gated, bounded).
=========================================================================

The Core runs a bounded autonomy loop on THIS machine:

    perceive  ->  propose  ->  (human approves via The Nexus)  ->  execute

What it may do autonomously (read-only / reversible):
  * read income CSVs dropped in runtime/inbox/ and compute sweep plans
  * read ledger, logs, worker reports; recompute audit snapshots
  * replicate ITSELF as bounded worker processes — but ONLY after a
    `spawn_worker` action has been APPROVED through The Nexus
  * execute an ibkr DCA plan — but ONLY after its `ibkr_order` action was
    APPROVED (the engine re-verifies approval + compliance internally, so
    the gate holds even if the Core is buggy)

What it may NEVER do:
  * execute anything without a recorded Nexus approval (fail-closed)
  * spawn from a worker (replication depth hard-capped at 1: Core -> worker)
  * exceed daily cycle / proposal / worker budgets (config.AUTOMATON_*)
  * run while runtime/automaton_STOP exists (kill switch, CLI or console)

Commands
--------
    python automaton.py run                 # one cycle (cron-friendly)
    python automaton.py daemon              # loop every CYCLE_INTERVAL_S
    python automaton.py status              # heartbeat, budgets, workers
    python automaton.py request-spawn --role ledger_audit
    python automaton.py workers             # list worker reports
    python automaton.py halt | resume       # kill switch
    python automaton.py worker-run --role R --task-id T --approval NX-...
                                            # internal: a replicated instance
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import config
import nexus_gate as nx
import worker_roles

STOP_FILE = config.RUNTIME_DIR / "automaton_STOP"
HEARTBEAT = config.RUNTIME_DIR / "automaton_heartbeat.json"
STATE_FILE = config.RUNTIME_DIR / "automaton_state.json"
INBOX = config.RUNTIME_DIR / "inbox"
WORKERS_DIR = config.RUNTIME_DIR / "workers"
INBOX.mkdir(parents=True, exist_ok=True)
WORKERS_DIR.mkdir(parents=True, exist_ok=True)

SPAWN_DEPTH = int(os.environ.get("JARVIS_SPAWN_DEPTH", "0"))


def _log(msg: str, level: str = "INFO") -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [AUTOMATON d{SPAWN_DEPTH}] [{level}] {msg}"
    print(line)
    with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except json.JSONDecodeError:
        _log(f"WARN: {path.name} corrupt; resetting to default", "WARN")
        return default


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _state() -> dict:
    return _read_json(STATE_FILE, {"total_cycles": 0, "cycles_per_day": {},
                                   "processed_inbox": [], "executed_actions": [],
                                   "spawned_workers": []})


def _save_state(s: dict) -> None:
    _write_json(STATE_FILE, s)


# ------------------------------------------------------------- kill switch --
def halted() -> bool:
    return STOP_FILE.exists()


def halt() -> None:
    STOP_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    _log("HALT requested — kill switch engaged; all loops stop after current step")


def resume() -> None:
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    _log("resume — kill switch released")


# ---------------------------------------------------------------- budgets --
def _budget_ok(state: dict) -> bool:
    today = time.strftime("%Y-%m-%d")
    used = state["cycles_per_day"].get(today, 0)
    if used >= config.AUTOMATON_MAX_CYCLES_PER_DAY:
        _log(f"daily cycle budget exhausted ({used}/{config.AUTOMATON_MAX_CYCLES_PER_DAY}) — sleeping", "WARN")
        return False
    return True


# ------------------------------------------------------------------ cycle --
def cycle() -> dict:
    """One perceive -> propose -> execute-approved pass. Returns a summary."""
    if halted():
        _log("STOP file present — cycle skipped (kill switch)")
        return {"skipped": "halted"}
    state = _state()
    if not _budget_ok(state):
        return {"skipped": "daily-budget"}
    summary = {"proposals": 0, "executions": 0, "spawns": 0, "workers_merged": 0}

    # ---- perceive: new income CSVs in the inbox -------------------------
    import cashflow_bridge as cb
    for csv_path in sorted(INBOX.glob("*.csv")):
        if csv_path.name in state["processed_inbox"]:
            continue
        try:
            rows = cb.read_income_csv(csv_path)
            for row in rows:
                plan = cb.compute_plan(row)
                cb.write_plan(plan)
                ids = cb.stage_transfers(plan)
                plan["nexus_action_ids"] = ids
                cb.write_plan(plan)
                summary["proposals"] += len(ids)
                _log(f"inbox {csv_path.name}: staged {ids or 'nothing (zero month)'}")
            state["processed_inbox"].append(csv_path.name)
        except (ValueError, OSError) as exc:
            _log(f"inbox {csv_path.name} rejected: {exc}", "ERROR")
            state["processed_inbox"].append(csv_path.name + " (rejected)")
        if summary["proposals"] >= config.AUTOMATON_MAX_PROPOSALS_PER_CYCLE:
            break

    # ---- execute: approved ibkr orders not yet executed ------------------
    import ibkr_dca_engine as eng
    pending = nx.list_pending() + [r for r in nx._read(config.NEXUS_PENDING)
                                   if r["status"] == "APPROVED"]
    for row in pending:
        if row["status"] != "APPROVED" or row["kind"] != "ibkr_order":
            continue
        if row["id"] in state["executed_actions"]:
            continue
        period = row["payload"].get("period")
        if not period:
            continue
        _log(f"executing APPROVED {row['id']} (plan {period}) on your behalf")
        rc = eng.run_period(period)
        state["executed_actions"].append(row["id"])
        if rc == 0:
            summary["executions"] += 1
        else:
            _log(f"execution of {row['id']} halted by engine (rc={rc}) — logged, not retried this cycle", "ERROR")

    # ---- replicate: approved spawn_worker actions -------------------------
    for row in pending:
        if row["status"] != "APPROVED" or row["kind"] != "spawn_worker":
            continue
        if row["id"] in state["executed_actions"]:
            continue
        role = row["payload"].get("role", "ledger_audit")
        wid = spawn_worker(role, approval_id=row["id"])
        state["executed_actions"].append(row["id"])
        if wid:
            summary["spawns"] += 1

    # ---- merge worker reports ---------------------------------------------
    for report in WORKERS_DIR.glob("*/report.json"):
        key = f"report:{report.parent.name}"
        if key in state["processed_inbox"]:
            continue
        data = _read_json(report, {})
        _log(f"worker {report.parent.name} ({data.get('role')}) reports: "
             f"{json.dumps(data.get('result', {}))[:200]}")
        state["processed_inbox"].append(key)
        summary["workers_merged"] += 1

    state["total_cycles"] += 1
    today = time.strftime("%Y-%m-%d")
    state["cycles_per_day"][today] = state["cycles_per_day"].get(today, 0) + 1
    _save_state(state)
    _write_json(HEARTBEAT, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "mode": "DRY-RUN" if config.DRY_RUN else "LIVE",
                            "cycle": state["total_cycles"], "depth": SPAWN_DEPTH,
                            "halted": halted(), "summary": summary,
                            "workers_active": _active_workers()})
    _log(f"cycle {state['total_cycles']} done: {summary}")
    return summary


# ------------------------------------------------------------- replication --
def _active_workers() -> list:
    out = []
    for wdir in sorted(WORKERS_DIR.iterdir()):
        rep = _read_json(wdir / "report.json", None)
        out.append({"id": wdir.name, "role": (rep or {}).get("role"),
                    "status": "done" if rep else "running/unknown",
                    "finished": (rep or {}).get("finished_at")})
    return out[-config.AUTOMATON_MAX_WORKERS * 2:]


def spawn_worker(role: str, approval_id: str = "") -> str | None:
    """Replicate the automaton as ONE bounded worker. Depth-capped: a worker
    can never spawn (JARVIS_SPAWN_DEPTH>=1 refuses)."""
    if SPAWN_DEPTH >= 1:
        _log("replication refused: workers cannot replicate (depth cap)", "ERROR")
        return None
    if role not in worker_roles.ROLES:
        _log(f"refused spawn: unknown role {role!r}", "ERROR")
        return None
    state = _state()
    active = [w for w in _active_workers() if w["status"] != "done"]
    if len(active) >= config.AUTOMATON_MAX_WORKERS:
        _log(f"replication deferred: {len(active)} workers active "
             f"(max {config.AUTOMATON_MAX_WORKERS})", "WARN")
        return None
    wid = f"W-{time.strftime('%Y%m%d-%H%M%S')}-{role}"
    wdir = WORKERS_DIR / wid
    wdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JARVIS_SPAWN_DEPTH="1")
    logf = open(wdir / "worker.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "automaton.py", "worker-run", "--role", role,
         "--task-id", wid, "--approval", approval_id],
        cwd=str(Path(__file__).resolve().parent), env=env,
        stdout=logf, stderr=subprocess.STDOUT)
    state["spawned_workers"].append({"id": wid, "role": role, "pid": proc.pid,
                                     "approval": approval_id,
                                     "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _save_state(state)
    _log(f"replicated as worker {wid} role={role} pid={proc.pid} "
         f"(approval {approval_id or 'n/a'})")
    return wid


def worker_run(role: str, task_id: str, approval: str) -> int:
    """The replicated instance: bounded, read-mostly, writes ONE report."""
    if SPAWN_DEPTH < 1:
        _log("worker-run must be launched by the Core (depth env missing)", "ERROR")
        return 1
    if halted():
        _log("worker aborted: kill switch engaged", "ERROR")
        return 1
    wdir = WORKERS_DIR / task_id
    wdir.mkdir(parents=True, exist_ok=True)
    fn = worker_roles.ROLES.get(role)
    if fn is None:
        _log(f"worker aborted: unknown role {role!r}", "ERROR")
        return 1
    try:
        result = fn()
        rc = 0
    except Exception as exc:  # noqa: BLE001 — a worker must die loudly, not hang
        _log(f"worker {task_id} failed: {exc}", "ERROR")
        result, rc = {"error": str(exc)}, 1
    _write_json(wdir / "report.json", {"role": role, "task_id": task_id,
                                       "approval": approval, "result": result,
                                       "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _log(f"worker {task_id} finished rc={rc}")
    return rc


# ------------------------------------------------------------------ daemon --
def daemon() -> int:
    _log(f"daemon starting (interval {config.AUTOMATON_CYCLE_INTERVAL_S}s, "
         f"max {config.AUTOMATON_MAX_CYCLES_PER_DAY} cycles/day, "
         f"mode {'DRY-RUN' if config.DRY_RUN else 'LIVE'})")
    try:
        while not halted():
            cycle()
            for _ in range(config.AUTOMATON_CYCLE_INTERVAL_S):
                if halted():
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    _log("daemon stopped")
    return 0


def status() -> dict:
    return {"heartbeat": _read_json(HEARTBEAT, {}), "state": _state(),
            "halted": halted(), "workers": _active_workers(),
            "pending": nx.list_pending(),
            "inbox": [p.name for p in INBOX.glob("*.csv")],
            "limits": {"max_cycles_per_day": config.AUTOMATON_MAX_CYCLES_PER_DAY,
                       "max_workers": config.AUTOMATON_MAX_WORKERS,
                       "max_proposals_per_cycle": config.AUTOMATON_MAX_PROPOSALS_PER_CYCLE}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Jarvis Central Automaton")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="one bounded cycle")
    sub.add_parser("daemon", help="loop until halt or Ctrl+C")
    sub.add_parser("status", help="heartbeat / budgets / workers")
    sub.add_parser("halt", help="engage kill switch")
    sub.add_parser("resume", help="release kill switch")
    sub.add_parser("workers", help="list replicated workers")
    rs = sub.add_parser("request-spawn", help="stage a replication for Nexus approval")
    rs.add_argument("--role", required=True, choices=sorted(worker_roles.ROLES))
    wr = sub.add_parser("worker-run", help="internal: replicated instance")
    wr.add_argument("--role", required=True)
    wr.add_argument("--task-id", required=True)
    wr.add_argument("--approval", default="")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        cycle()
        return 0
    if args.cmd == "daemon":
        return daemon()
    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if args.cmd == "halt":
        halt()
        return 0
    if args.cmd == "resume":
        resume()
        return 0
    if args.cmd == "workers":
        for w in _active_workers():
            print(f"{w['id']:<28} {str(w['role']):<16} {w['status']}")
        return 0
    if args.cmd == "request-spawn":
        aid = nx.stage("spawn_worker", {"role": args.role,
                                        "note": "Core replication request — "
                                                "worker is bounded, read-mostly, "
                                                "depth-capped"},
                       proposed_by="Central Automaton (Core)")
        print(f"staged {aid} — approve in The Nexus to let the Core replicate.")
        return 0
    if args.cmd == "worker-run":
        return worker_run(args.role, args.task_id, args.approval)
    return 1


if __name__ == "__main__":
    sys.exit(main())
