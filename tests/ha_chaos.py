"""
tests/ha_chaos.py

Chaos suite for the 2-node HA story. Spins up an in-process api.app
instance with the relevant env (LOCALLYAI_NODE_ID / LOG_DIR /
SHARED_DIR) per test, so each invariant is checked in isolation. The
two-node behaviour is asserted by tearing down "node A" state and
re-importing as "node B" against the same SHARED_DIR — exactly what
happens in production when a real second box joins the fleet.

Run from the repo root:

    .venv/bin/python tests/ha_chaos.py

Exit 0 on success; non-zero on first failure.
"""
from __future__ import annotations
import importlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO / ".env")

ADMIN_KEY = os.environ.get("LOCALLYAI_ADMIN_KEY", "")
USER_KEY  = "a" * 64  # deterministic test fixture, not a real key

PASS, FAIL = 0, 0


def _ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"\033[32m  PASS\033[0m  {msg}")


def _fail(msg: str, exc: Exception | None = None) -> None:
    global FAIL
    FAIL += 1
    print(f"\033[31m  FAIL\033[0m  {msg}")
    if exc:
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def _section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


# ── Per-node fixture (rebuilds api module state on each call) ───────────────

def _bring_up(node_id: str, log_dir: Path, shared_dir: Path):
    """(Re)load api/config/fleet for the given identity. Returns
    (api_module, TestClient, mock_inference_callable, audit_lines_fn)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.mkdir(parents=True, exist_ok=True)

    os.environ["LOCALLYAI_NODE_ID"]   = node_id
    os.environ["LOCALLYAI_LOG_DIR"]   = str(log_dir)
    os.environ["LOCALLYAI_SHARED_DIR"] = str(shared_dir)

    # Drop everything LocallyAI from sys.modules so the next import builds
    # fresh module state under the new env. importlib.reload alone wasn't
    # enough — api imports config/fleet/sentinel transitively, and reload
    # doesn't re-execute the import order in a way that resets all the
    # module-level constants we depend on.
    for mod_name in list(sys.modules):
        if mod_name == "api" or mod_name == "config" or mod_name == "fleet" \
           or mod_name == "sync_conflicts" or mod_name == "platform_compat" \
           or mod_name == "shared_lock" or mod_name == "mlx_inference" \
           or mod_name == "os_supervisor" or mod_name.startswith("watchdog"):
            sys.modules.pop(mod_name, None)
    import api as api_mod  # noqa: WPS433  — fresh import under new env
    sys.modules["api"] = api_mod

    counter = {"n": 0}
    def _fake_infer(messages, model, stream, max_tokens, temperature):
        counter["n"] += 1
        return f"answer-from-{node_id}-{counter['n']}"
    api_mod._infer = _fake_infer

    from fastapi.testclient import TestClient
    client = TestClient(api_mod.app)

    try:
        api_mod._register_fleet()
    except Exception:
        pass

    def audit_lines() -> int:
        f = log_dir / "audit.log"
        return f.read_text().count("\n") if f.exists() else 0

    return api_mod, client, counter, audit_lines


def _chat(client, content: str, *, client_request_id: str | None = None,
          stream: bool = False) -> dict:
    body = {"messages": [{"role": "user", "content": content}],
            "max_tokens": 5, "stream": stream}
    if client_request_id:
        body["client_request_id"] = client_request_id
    r = client.post("/v1/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {USER_KEY}"})
    return {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else None}


def _admin(client, path: str) -> dict:
    r = client.get(path, headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    return {"status": r.status_code, "body": r.json()}


# ── Tests ───────────────────────────────────────────────────────────────────

def main() -> int:
    if not ADMIN_KEY:
        print("LOCALLYAI_ADMIN_KEY missing from .env — cannot run chaos suite")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="locallyai-chaos-"))
    print(f"Workdir: {workdir}")
    shared = workdir / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    real_users = REPO / "users.json"
    if real_users.exists():
        shutil.copy(real_users, shared / "users.json")

    try:
        # ── Idempotency ─────────────────────────────────────────────────────
        _section("Idempotency: same id -> cached, no double audit/billing")
        try:
            api_a, client_a, counter, lines_a = _bring_up(
                "chaos-a", workdir / "node-a-logs", shared)
            pre = lines_a()
            rid = "chaos-rid-001"
            r1 = _chat(client_a, "hello world", client_request_id=rid)
            r2 = _chat(client_a, "hello world", client_request_id=rid)
            assert r1["status"] == 200, r1
            assert r2["status"] == 200, r2
            assert r1["body"]["choices"][0]["message"]["content"] == \
                   r2["body"]["choices"][0]["message"]["content"], \
                   (r1["body"], r2["body"])
            assert counter["n"] == 1, f"_infer should be called once; got {counter['n']}"
            assert lines_a() - pre == 1, f"audit delta must be 1; got {lines_a() - pre}"
            _ok(f"same id → cached body, _infer count={counter['n']}, audit delta=1")
        except Exception as e:
            _fail("idempotency cache", e)

        # ── Cross-node retry: peer never has the original ───────────────────
        _section("Cross-node retry: peer (different process) executes fresh")
        try:
            # Bring up node-b with the same SHARED_DIR but a fresh log dir.
            api_b, client_b, counter_b, lines_b = _bring_up(
                "chaos-b", workdir / "node-b-logs", shared)
            pre_b = lines_b()
            r3 = _chat(client_b, "hello world", client_request_id="chaos-rid-001")
            assert r3["status"] == 200, r3
            assert lines_b() - pre_b == 1, \
                f"node B should write 1 audit line; got {lines_b() - pre_b}"
            assert "chaos-b" in r3["body"]["choices"][0]["message"]["content"], r3["body"]
            _ok("node B executes fresh and writes its own audit line "
                "(per-node cache → no false dedup across processes)")
        except Exception as e:
            _fail("cross-node retry behaviour", e)

        # ── Per-node audit chain ────────────────────────────────────────────
        _section("Per-node audit chain on each node")
        try:
            for node_id, log_dir in (("chaos-a", workdir / "node-a-logs"),
                                     ("chaos-b", workdir / "node-b-logs")):
                api_x, client_x, _, _ = _bring_up(node_id, log_dir, shared)
                r = _admin(client_x, "/admin/audit-verify")
                assert r["status"] == 200, r
                assert r["body"].get("status") == "ok", r["body"]
                _ok(f"{node_id}: chain ok ({r['body'].get('entries')} entries)")
        except Exception as e:
            _fail("per-node audit chain", e)

        # ── Tail truncation ────────────────────────────────────────────────
        _section("Tail truncation detected by the verifier")
        try:
            api_x, client_x, _, _ = _bring_up(
                "chaos-tail", workdir / "node-tail-logs", shared)
            # Write one entry first.
            _chat(client_x, "yo", client_request_id="tail-1")
            chain_state = (workdir / "node-tail-logs") / ".audit_chain"
            audit_path  = (workdir / "node-tail-logs") / "audit.log"
            assert audit_path.exists() and chain_state.exists(), "fixture should have written"
            audit_path.write_text("")  # truncate live log; chain head still set
            r = _admin(client_x, "/admin/audit-verify")
            assert r["status"] == 200 and r["body"].get("status") == "TAMPERED", r["body"]
            _ok(f"truncated audit.log → status TAMPERED "
                f"(reason: {(r['body'].get('reason') or '')[:80]})")
        except Exception as e:
            _fail("tail-truncation detection", e)

        # ── Fan-out endpoint sees both nodes ────────────────────────────────
        _section("/admin/fleet/audit-verify aggregates both nodes from fleet.json")
        try:
            # Bring up node-a fresh; fleet.json should still list both ids
            # because we never deregistered. The fan-out will try the peer
            # over HTTP — in tests the peer URL is a https://chaos-b:8000
            # placeholder which will fail to connect; the endpoint must
            # report it as "unreachable" rather than crash.
            api_a, client_a, _, _ = _bring_up(
                "chaos-a", workdir / "node-a-logs", shared)
            r = _admin(client_a, "/admin/fleet/audit-verify")
            assert r["status"] == 200, r
            ids = {n.get("node_id") for n in r["body"].get("nodes", [])}
            assert "chaos-a" in ids, ids
            for entry in r["body"]["nodes"]:
                if entry.get("node_id") != "chaos-a":
                    assert entry.get("status") in ("ok", "unreachable"), entry
            _ok(f"fleet endpoint returned {len(ids)} entries; aggregation working")
        except Exception as e:
            _fail("fleet audit-verify aggregation", e)

        # ── Sync conflict quarantine ────────────────────────────────────────
        _section("Sync conflict file is quarantined into SHARED_DIR/conflicts/")
        try:
            fake = shared / "users.sync-conflict-20260504-180312-AABBCC.json"
            fake.write_text('{"fake": true}')
            from sync_conflicts import scan_and_alert  # noqa: WPS433
            events = scan_and_alert(shared)
            assert events and events[0]["original"] == "users.json", events
            quarantined = shared / "conflicts" / fake.name
            assert quarantined.exists(), "conflict file should be moved to conflicts/"
            assert not fake.exists(), "conflict file should not remain in live tree"
            _ok(f"conflict quarantined to {quarantined.relative_to(shared)}")
        except Exception as e:
            _fail("sync-conflict quarantine", e)

        # ── Erasure ledger blocks audit writes for erased pseudonyms ────────
        _section("Erasure ledger blocks new audit writes for erased pseudonyms")
        try:
            api_x, client_x, _, lines_x = _bring_up(
                "chaos-erase", workdir / "node-erase-logs", shared)
            from config import pseudonymise_user, ERASURE_LOG  # noqa: WPS433
            import config as _cfg
            pseudonym = pseudonymise_user("Admin")
            ERASURE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ERASURE_LOG, "a") as f:
                f.write(json.dumps({
                    "timestamp": "2026-05-04T00:00:00Z",
                    "event": "erasure", "pseudonym": pseudonym,
                }) + "\n")
            _cfg._ERASED = _cfg._load_erased()
            _cfg._ERASURE_MTIME = ERASURE_LOG.stat().st_mtime
            pre = lines_x()
            r = _chat(client_x, "should be refused")
            assert lines_x() == pre, \
                f"audit must not grow for erased pseudonym; pre={pre} post={lines_x()}"
            _ok(f"erased pseudonym → 0 new audit lines (status {r['status']})")
            ERASURE_LOG.unlink(missing_ok=True)
            _cfg._ERASED = set()
        except Exception as e:
            _fail("erasure ledger", e)

        # ── Streaming SSE ───────────────────────────────────────────────────
        _section("Streaming SSE branch + idempotency cache fills with assembled answer")
        try:
            api_x, client_x, _, lines_x = _bring_up(
                "chaos-stream", workdir / "node-stream-logs", shared)
            import mlx_inference as _mlx
            def _fake_stream(messages, model, max_tokens, temperature):
                for tok in ["alpha", " ", "beta"]:
                    yield tok
            _mlx.stream_tokens = _fake_stream
            api_x.BACKEND = "mlx"  # force the streaming branch
            pre = lines_x()
            with client_x.stream("POST", "/v1/chat/completions",
                                 json={"messages": [{"role": "user", "content": "stream test"}],
                                       "max_tokens": 5, "stream": True,
                                       "client_request_id": "chaos-stream-001"},
                                 headers={"Authorization": f"Bearer {USER_KEY}"}) as resp:
                assert resp.status_code == 200, (resp.status_code, resp.read())
                lines = [l for l in resp.iter_lines() if l]
            assert any("alpha" in l for l in lines), lines[:3]
            assert any("[DONE]" in l for l in lines), lines[-3:]
            assert lines_x() - pre == 1, \
                f"streaming should write 1 audit line; got {lines_x() - pre}"
            # Replay same id non-streaming → cached body served (0 extra writes).
            r2 = _chat(client_x, "stream test", client_request_id="chaos-stream-001")
            assert r2["status"] == 200, r2
            assert "alpha" in r2["body"]["choices"][0]["message"]["content"], r2["body"]
            assert lines_x() - pre == 1, \
                f"replay must hit cache (0 extra audit); got {lines_x() - pre - 1} extra"
            _ok("SSE delivered tokens, [DONE] sent, 1 audit line; cached replay = 0 extra audits")
        except Exception as e:
            _fail("streaming SSE", e)

        # ── Salt rotation: chain stays valid; correlation broken ────────────
        _section("Salt rotation: chain valid across boundary; correlation broken")
        try:
            api_x, client_x, _, lines_x = _bring_up(
                "chaos-salt", workdir / "node-salt-logs", shared)
            from config import pseudonymise_user, current_salt_era
            old_era    = current_salt_era()
            old_pseudo = pseudonymise_user("Admin")
            assert old_pseudo, "salt must be set; check .env"

            # Write 3 entries under the current salt.
            for i in range(3):
                r = _chat(client_x, f"pre-rotation {i}",
                          client_request_id=f"chaos-salt-pre-{i}")
                assert r["status"] == 200, r

            # Rotate. The function rewrites .env, retires the old salt to
            # ERA_1, and stamps a boundary entry under the OLD salt.
            from manage_users import rotate_audit_salt
            result = rotate_audit_salt(keep_eras=4)
            new_era = result["new_era"]
            assert new_era != old_era, (old_era, new_era)
            assert result["previous_era"] == old_era, result

            # Re-load .env into os.environ — in production this happens via
            # the service restart; we simulate. dotenv defaults to NOT
            # overriding existing env vars, so we explicitly override.
            from dotenv import load_dotenv as _ld
            _ld(REPO / ".env", override=True)

            # Re-import api/config so the API picks up the new salt.
            api_x, client_x, _, lines_x = _bring_up(
                "chaos-salt", workdir / "node-salt-logs", shared)
            from config import pseudonymise_user as p_now, current_salt_era as e_now
            new_pseudo_admin = p_now("Admin")
            assert new_pseudo_admin and new_pseudo_admin != old_pseudo, \
                f"new-era pseudonym must differ from old-era: {old_pseudo} vs {new_pseudo_admin}"
            assert e_now() == new_era, (e_now(), new_era)

            # Old-era pseudonym still recoverable for subject-access.
            old_pseudo_via_era = p_now("Admin", era=old_era)
            assert old_pseudo_via_era == old_pseudo, \
                f"era-targeted lookup must return historic pseudonym: {old_pseudo_via_era} vs {old_pseudo}"

            # Write 3 more entries under the NEW salt.
            for i in range(3):
                r = _chat(client_x, f"post-rotation {i}",
                          client_request_id=f"chaos-salt-post-{i}")
                assert r["status"] == 200, r

            # Audit chain spans the boundary and is still ok.
            verify = _admin(client_x, "/admin/audit-verify")
            assert verify["status"] == 200, verify
            assert verify["body"].get("status") == "ok", verify["body"]

            # Last entry on disk carries the NEW era.
            from config import LOG_DIR
            tail_line = (LOG_DIR / "audit.log").read_text().splitlines()[-1]
            tail = json.loads(tail_line)
            assert tail.get("salt_era") == new_era, tail
            _ok(f"old era {old_era} retired; new era {new_era} active; "
                f"chain ok across boundary; old pseudonym still resolvable "
                f"for subject-access; new entries carry new era stamp")
        except Exception as e:
            _fail("salt rotation", e)

        # ── Streaming abort: closed consumer must not wedge the MLX worker ──
        _section("Streaming abort: consumer disconnect mid-stream releases worker")
        try:
            api_x, client_x, _, _ = _bring_up(
                "chaos-abort", workdir / "node-abort-logs", shared)
            import mlx_inference as _mlx
            import time as _time, threading as _thr

            # Fake mlx_lm.stream_generate that yields slowly so we can close
            # the consumer mid-stream. Inject into the module namespace so
            # _stream_pump_sync's `from mlx_lm import stream_generate` picks
            # it up.
            class _FakeResponse:
                def __init__(self, text): self.text = text
            yields_made = {"n": 0}
            stop_signalled_at = {"v": 0}
            def _fake_stream_generate(model, tokenizer, prompt, **kw):
                for i in range(200):
                    yields_made["n"] = i + 1
                    yield _FakeResponse(f"tok{i} ")
                    _time.sleep(0.05)
            class _FakeMlxLm:
                stream_generate = staticmethod(_fake_stream_generate)
                class sample_utils:
                    make_sampler = staticmethod(lambda **kw: None)
                    make_logits_processors = staticmethod(lambda **kw: None)
            sys.modules["mlx_lm"] = _FakeMlxLm
            sys.modules["mlx_lm.sample_utils"] = _FakeMlxLm.sample_utils

            # Pretend the model is loaded so _ensure_loaded_sync is a no-op.
            _mlx._model = object()
            class _FakeTok:
                def apply_chat_template(self, *a, **kw):
                    return "prompt"
            _mlx._tokenizer = _FakeTok()

            # Stream 1: pull a few tokens, then close() before completion.
            gen = _mlx.stream("hello", max_tokens=200, temperature=0.1)
            received = []
            for i, tok in enumerate(gen):
                received.append(tok)
                if i >= 2:
                    stop_signalled_at["v"] = yields_made["n"]
                    gen.close()  # GeneratorExit → finally → abort_event.set()
                    break

            # Give the producer up to 2s to notice the abort and exit.
            deadline = _time.monotonic() + 2.0
            n_at_close = stop_signalled_at["v"]
            while _time.monotonic() < deadline:
                _time.sleep(0.1)
                # If yields_made stopped advancing, the producer has exited.
                last = yields_made["n"]
                _time.sleep(0.2)
                if yields_made["n"] == last:
                    break

            n_after_settle = yields_made["n"]
            assert n_after_settle - n_at_close < 50, (
                f"producer should have stopped within ~10 tokens of consumer "
                f"close; advanced from {n_at_close} to {n_after_settle}")

            # Stream 2: a fresh consumer must still get tokens. If the worker
            # was wedged by stream 1, this would hang here.
            yields_made["n"] = 0
            gen2 = _mlx.stream("again", max_tokens=10, temperature=0.1)
            ok = False
            try:
                for i, tok in enumerate(gen2):
                    if i == 2:
                        ok = True
                        gen2.close()
                        break
            finally:
                pass
            assert ok, "second stream did not produce tokens — worker is wedged"
            _ok(f"consumer closed at token {n_at_close}; producer halted by "
                f"token {n_after_settle}; second stream served fresh")
        except Exception as e:
            _fail("streaming abort: closed consumer must release worker", e)

        # ── Concurrency gate: queueing + 503 backpressure ───────────────────
        _section("Concurrency gate: 12 simultaneous requests, gate=2 inflight, queue=20")
        try:
            api_x, client_x, _, lines_x = _bring_up(
                "chaos-gate", workdir / "node-gate-logs", shared)
            import inference_gate as _ig
            _ig.configure_for_tests(max_inflight=2, max_queue=20)

            # Slow the inference path so the queue actually stacks up; without
            # this, mocked _infer is so fast that all 12 requests blast through
            # before the second one even starts queueing.
            import time as _time
            def _slow_infer(messages, model, stream, max_tokens, temperature):
                _time.sleep(0.15)
                return "ok"
            api_x._infer = _slow_infer

            import threading
            results: list[int] = []
            errors:  list[BaseException] = []
            peak_inflight = {"v": 0}
            peak_queued   = {"v": 0}

            def _watcher(stop_evt: threading.Event):
                while not stop_evt.is_set():
                    s = _ig.stats()
                    if s["in_flight"] > peak_inflight["v"]:
                        peak_inflight["v"] = s["in_flight"]
                    if s["queued"] > peak_queued["v"]:
                        peak_queued["v"] = s["queued"]
                    _time.sleep(0.005)

            stop = threading.Event()
            watcher = threading.Thread(target=_watcher, args=(stop,))
            watcher.start()

            def _fire(i: int):
                try:
                    r = _chat(client_x, f"concurrent {i}",
                              client_request_id=f"chaos-conc-{i:02d}")
                    results.append(r["status"])
                except BaseException as exc:
                    errors.append(exc)

            pre = lines_x()
            workers = [threading.Thread(target=_fire, args=(i,)) for i in range(12)]
            for w in workers: w.start()
            for w in workers: w.join()
            stop.set(); watcher.join()

            assert not errors, f"workers raised: {errors!r}"
            assert all(s == 200 for s in results), f"all should be 200; got {results}"
            assert lines_x() - pre == 12, \
                f"expected 12 audit lines; got {lines_x() - pre}"
            assert peak_inflight["v"] <= 2, \
                f"in_flight must NEVER exceed gate (2); peaked at {peak_inflight['v']}"
            assert peak_queued["v"] >= 1, \
                f"queue should have stacked; peak was {peak_queued['v']}"
            final_stats = _ig.stats()
            assert final_stats["total_admitted"] == 12, final_stats
            assert final_stats["total_rejected"] == 0, final_stats
            _ok(f"12 concurrent requests served; "
                f"peak in_flight={peak_inflight['v']}/2 "
                f"peak queue={peak_queued['v']}/20  "
                f"all 12 audit lines written; 0 rejected")
        except Exception as e:
            _fail("concurrency gate (queueing)", e)

        # ── Concurrency gate: queue full → 503 backpressure ─────────────────
        _section("Concurrency gate: 8 simultaneous, gate=1 inflight, queue=2 → expect 503s")
        try:
            api_x, client_x, _, lines_x = _bring_up(
                "chaos-gate2", workdir / "node-gate2-logs", shared)
            import inference_gate as _ig
            _ig.configure_for_tests(max_inflight=1, max_queue=2)

            import time as _time
            def _slow_infer(messages, model, stream, max_tokens, temperature):
                _time.sleep(0.4)
                return "ok"
            api_x._infer = _slow_infer

            import threading
            statuses: list[int] = []
            def _fire(i: int):
                r = _chat(client_x, f"flood {i}",
                          client_request_id=f"chaos-flood-{i:02d}")
                statuses.append(r["status"])
            workers = [threading.Thread(target=_fire, args=(i,)) for i in range(8)]
            for w in workers: w.start()
            for w in workers: w.join()

            ok_count   = sum(1 for s in statuses if s == 200)
            busy_count = sum(1 for s in statuses if s == 503)
            assert ok_count + busy_count == 8, statuses
            assert busy_count >= 1, f"flood at gate=1/queue=2 should produce 503s; statuses={statuses}"
            stats = _ig.stats()
            assert stats["total_rejected"] == busy_count, \
                f"gate counter ({stats['total_rejected']}) must match observed 503s ({busy_count})"
            _ok(f"under flood: {ok_count} served, {busy_count} got clean 503 "
                f"(smart-client retry-on-peer signal); gate counter consistent")
        except Exception as e:
            _fail("concurrency gate (backpressure)", e)

        # ── Summary ─────────────────────────────────────────────────────────
        print()
        print("─" * 60)
        print(f"  pass={PASS}  fail={FAIL}")
        print("─" * 60)
        return 0 if FAIL == 0 else 1
    finally:
        if FAIL == 0:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"\n(workdir preserved for inspection: {workdir})")


if __name__ == "__main__":
    sys.exit(main())
