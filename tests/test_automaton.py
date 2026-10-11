"""End-to-end tests for the Sovereign Multi-Agent AI Automaton."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automaton.core.approval import ApprovalGateway
from automaton.core.auth_manager import AuthManager, decrypt_secret, encrypt_secret
from automaton.core.constitution import load_constitution
from automaton.core.governance import (
    OutputContractError,
    PydanticOutputParser,
    extract_json,
    redact_secrets,
)
from automaton.core.model_router import ModelRouter, RouterConfig
from automaton.core.policy_engine import ActionDescriptor, PolicyEngine
from automaton.core.types import (
    ApprovalStatus,
    AuditEvent,
    MemoryKind,
    PolicyDecision,
    RiskLevel,
    TaskPlan,
)
from automaton.engine import Automaton
from automaton.memory.store import MemoryStore, RetrievalCompiler
from automaton.tools.registry import safe_eval_math


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def tmp_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    import json, shutil
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fixtures")
    for f in os.listdir(src):
        shutil.copy(os.path.join(src, f), fixtures / f)
    return {"tmp": tmp_path, "fixtures": str(fixtures)}


@pytest.fixture()
def automaton(tmp_data):
    return Automaton(fixtures_dir=tmp_data["fixtures"],
                     vault_path=str(tmp_data["tmp"] / "vault.json"),
                     memory_path=str(tmp_data["tmp"] / "mem.sqlite"))


# --------------------------------------------------------------------------- #
# Constitution
# --------------------------------------------------------------------------- #
def test_constitution_loads():
    c = load_constitution()
    assert c["hallucination_control"]["forbid_guessing"] is True
    assert len(c["rules"]) == 6
    assert any(r["id"] == "R-001" and r["risk"] == "critical" for r in c["rules"])


# --------------------------------------------------------------------------- #
# Governance: PydanticOutputParser retry loop
# --------------------------------------------------------------------------- #
def test_extract_json_from_fenced_and_prose():
    assert extract_json('Here you go:\n```json\n{"a": 1}\n```\ndone') == '{"a": 1}'
    assert extract_json('noise {"a": {"b": 2}} noise').startswith('{"a"')


def test_parser_retry_loop_corrects_malformed_output():
    """The classic local-model defect: bad JSON first, corrected after prompt."""
    calls = []

    def flaky_llm(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return '{"objective": "x", steps: [oops'   # malformed
        if len(calls) == 2:
            assert "NOT valid" in prompt and "SCHEMA" in prompt  # corrective prompt
        return '{"objective": "ship it", "steps": [], "estimated_risk": "low", "domains": ["coding"]}'

    audit: list[AuditEvent] = []
    parser = PydanticOutputParser(TaskPlan, max_retries=3, audit_log=audit)
    plan = parser.parse_with_retry(flaky_llm, "make a plan")
    assert plan.objective == "ship it"
    assert len(calls) == 2
    events = [e.event for e in audit]
    assert events == ["output_rejected", "output_validated"]


def test_parser_raises_after_max_retries():
    def always_bad(_: str) -> str:
        return "I am not a robot. No JSON here."

    parser = PydanticOutputParser(TaskPlan, max_retries=2)
    with pytest.raises(OutputContractError):
        parser.parse_with_retry(always_bad, "plan this")


def test_redact_secrets():
    out = redact_secrets("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 and sk-abcdefghijklmnopqrstuvwx")
    assert "ghp_ABCDEF" not in out and "sk-abcdefghij" not in out
    assert "[REDACTED_GH_TOKEN]" in out and "[REDACTED_API_KEY]" in out


# --------------------------------------------------------------------------- #
# Policy engine
# --------------------------------------------------------------------------- #
def test_policy_denies_financial_trades():
    pe = PolicyEngine()
    v = pe.evaluate("t1", ActionDescriptor(text="buy 100 shares of AAPL and place order now",
                                           domains=["financial"]))
    assert v.decision == PolicyDecision.DENIED
    assert v.risk == RiskLevel.CRITICAL
    assert "R-001" in v.matched_rules


def test_policy_low_risk_readonly_autonomous():
    pe = PolicyEngine()
    v = pe.evaluate("t2", ActionDescriptor(text="summarize my obsidian notes",
                                           tools=["obsidian.read"]))
    assert v.decision == PolicyDecision.AUTONOMOUS


def test_policy_high_risk_external_communication():
    pe = PolicyEngine()
    v = pe.evaluate("t3", ActionDescriptor(text="post to instagram about launch",
                                           tools=["instagram.post"],
                                           domains=["communication"]))
    assert v.decision == PolicyDecision.APPROVAL_REQUIRED
    assert v.risk == RiskLevel.HIGH


def test_policy_medium_code_deploy_needs_approval():
    pe = PolicyEngine()
    v = pe.evaluate("t4", ActionDescriptor(text="write python script and deploy to github repo",
                                           tools=["github.commit_and_push"],
                                           domains=["coding"]))
    assert v.decision == PolicyDecision.APPROVAL_REQUIRED
    assert v.risk == RiskLevel.MEDIUM


# --------------------------------------------------------------------------- #
# Auth manager
# --------------------------------------------------------------------------- #
def test_vault_encrypt_roundtrip_and_tamper():
    key = b"x" * 32
    blob = encrypt_secret(key, "my-secret-pat")
    assert decrypt_secret(key, blob) == "my-secret-pat"
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        decrypt_secret(key, tampered)


def test_auth_handle_ttl_and_repr(tmp_data):
    auth = AuthManager(vault_path=str(tmp_data["tmp"] / "v.json"), master_key=b"k" * 32)
    auth.store_pat("github", "ghp_" + "A" * 40)
    h = auth.get_handle("github")
    assert "ghp_" not in repr(h) and "ghp_" not in str(h)
    assert AuthManager.execute_with(h, lambda s: s.startswith("ghp_")) is True
    h._issued -= 60  # force expiry
    with pytest.raises(RuntimeError):
        h.reveal()


def test_vault_file_has_no_plaintext(tmp_data):
    p = str(tmp_data["tmp"] / "v2.json")
    auth = AuthManager(vault_path=p, master_key=b"k" * 32)
    auth.store_pat("github", "ghp_SUPERSECRETTOKENVALUE1234567890")
    raw = open(p).read()
    assert "ghp_SUPERSECRET" not in raw


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
def test_memory_search_and_retrieval_budget(tmp_data):
    store = MemoryStore(db_path=str(tmp_data["tmp"] / "m.sqlite"))
    store.add(MemoryKind.SEMANTIC, "Invoice triage workflow takes 40 minutes daily in Notion db-ops")
    store.add(MemoryKind.SEMANTIC, "The cat sat on the mat")
    res = store.search("invoice triage automation notion", kinds=[MemoryKind.SEMANTIC], top_k=1)
    assert res and "Invoice" in res[0].content
    compiler = RetrievalCompiler(store, char_budget=200)
    ctx = compiler.compile("invoice triage")
    assert "Invoice" in ctx and len(ctx) <= 400
    store.close()


# --------------------------------------------------------------------------- #
# Deterministic math tool (hallucination control)
# --------------------------------------------------------------------------- #
def test_safe_eval_math():
    assert safe_eval_math("2*(3+4)/7") == 2.0
    with pytest.raises(ValueError):
        safe_eval_math("__import__('os').system('ls')")


# --------------------------------------------------------------------------- #
# Sandbox
# --------------------------------------------------------------------------- #
def test_sandbox_blocks_env_secrets(automaton):
    code = (
        "import os\nprint(os.environ.get('AUTOMATON_VAULT_KEY', 'NO_SECRET'))\n"
    )
    os.environ["AUTOMATON_VAULT_KEY"] = "leakme123"
    res = automaton.tools.call("sandbox.run", {"code": code, "timeout_s": 15})
    del os.environ["AUTOMATON_VAULT_KEY"]
    assert res.ok and "leakme123" not in res.output["stdout"]


def test_sandbox_timeout(automaton):
    res = automaton.tools.call("sandbox.run",
                               {"code": "import time; time.sleep(5)", "timeout_s": 1})
    assert not res.ok and "timeout" in (res.error or "").lower()


# --------------------------------------------------------------------------- #
# Approval gateway
# --------------------------------------------------------------------------- #
def test_gateway_expiry_auto_rejects():
    from automaton.core.types import PolicyVerdict, Domain, Reversibility
    gw = ApprovalGateway(expiry_hours=0)  # instant expiry
    verdict = PolicyVerdict(task_id="t", decision=PolicyDecision.APPROVAL_REQUIRED,
                            risk=RiskLevel.HIGH, reversibility=Reversibility.IRREVERSIBLE,
                            domain=Domain.COMMUNICATION)
    req = gw.request("t", verdict, action_summary="send email", worst_case="reputation",
                     notify=lambda r: None)
    assert gw.pending() == []
    assert gw.get(req.id).status == ApprovalStatus.EXPIRED


# --------------------------------------------------------------------------- #
# End-to-end pipeline (the blueprint scenario)
# --------------------------------------------------------------------------- #
OBJECTIVE = ("Analyze my Notion databases, find inefficiencies in my current workflows, "
             "write a Python script to automate them, and deploy it to my GitHub")


def _submit(automaton):
    automaton.auth.store_pat("github", "ghp_TESTTESTTESTTESTTEST1234567890")
    return automaton.submit(OBJECTIVE, context={"notion_database": "db-ops-workflows"})


def test_e2e_parks_at_approval_then_completes(automaton):
    run = _submit(automaton)
    # read-only & sandbox steps ran autonomously; deployment parked
    assert run.status == "awaiting_approval"
    statuses = {s.title: s.status.value for s in run.plan.steps}
    assert statuses["Query Notion databases"] == "done"
    assert statuses["Deploy to GitHub"] == "awaiting_approval"

    appr = run.approvals[-1]
    req = automaton.gateway.get(appr)
    assert req.diff_preview and "workflow" in req.diff_preview.lower()
    assert req.worst_case and req.evidence

    automaton.gateway.decide(appr, approve=True)
    run = automaton.resume(run)
    assert run.completed and run.status == "completed"

    deployed = [s for s in run.plan.steps if s.tool == "github.commit_and_push"][0]
    assert deployed.result["output"]["simulated"] is True
    ep = automaton.memory.recent(MemoryKind.EPISODIC, limit=5)
    assert any("COMPLETED" in r.content for r in ep)


def test_e2e_rejection_skips_deployment(automaton):
    run = _submit(automaton)
    appr = run.approvals[-1]
    automaton.gateway.decide(appr, approve=False)
    run = automaton.resume(run)
    deploy = [s for s in run.plan.steps if s.tool == "github.commit_and_push"][0]
    assert deploy.status.value == "skipped"
    assert not run.completed


def test_e2e_financial_trade_denied_before_anything(automaton):
    run = automaton.submit("Execute financial trades: buy stock TSLA, place order immediately")
    assert run.status == "denied"
    assert run.plan is None  # never even planned
    assert "R-001" in (run.denied_reason or "") or "trade" in (run.denied_reason or "").lower()
    ep = automaton.memory.recent(MemoryKind.EPISODIC, limit=3)
    assert any("DENIED" in r.content for r in ep)


def test_governance_retry_exercised_in_real_pipeline(automaton):
    _submit(automaton)
    rejects = [e for e in automaton.audit if e.event == "output_rejected"
               and e.detail.get("schema") == "TaskPlan"]
    validates = [e for e in automaton.audit if e.event == "output_validated"
                 and e.detail.get("schema") == "TaskPlan"]
    assert rejects and validates  # malformed local output auto-corrected silently


def test_orchestrator_never_sees_credentials(automaton):
    specs = automaton.tools.specs()
    blob = str(specs).lower()
    assert "pat" not in blob.replace("parameters", "")
    assert all(s["requires_credential"] in (True, False) for s in specs)
    # and prompts built by the brain contain no token material
    from automaton.core.types import TaskRequest
    prompt = automaton.brain.plan_prompt(TaskRequest(objective=OBJECTIVE), specs)
    assert "ghp_" not in prompt


# --------------------------------------------------------------------------- #
# FastAPI control plane
# --------------------------------------------------------------------------- #
def test_api_flow(tmp_data):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from automaton.api.main import app, set_engine

    auto = Automaton(fixtures_dir=tmp_data["fixtures"],
                     vault_path=str(tmp_data["tmp"] / "v.json"),
                     memory_path=str(tmp_data["tmp"] / "m.sqlite"))
    set_engine(auto)
    client = TestClient(app)

    assert client.get("/health").json()["status"] == "ok"
    r = client.post("/tasks", json={"objective": OBJECTIVE}).json()
    assert r["status"] == "awaiting_approval"
    approvals = client.get("/approvals").json()
    pending = [a for a in approvals if a["status"] == "pending"]
    assert pending
    d = client.post(f"/approvals/{pending[0]['id']}/decision", json={"approve": True})
    assert d.json()["status"] == "approved"
    r2 = client.post(f"/tasks/{r['task_id']}/resume").json()
    assert r2["status"] == "completed"
    denied = client.post("/tasks", json={"objective": "place order to buy stock NVDA now"}).json()
    assert denied["status"] == "denied"
    audit = client.get("/audit").json()
    assert any(e["actor"] == "policy_engine" for e in audit)
