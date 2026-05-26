import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

LOG_DIR  = Path(__file__).resolve().parent.parent / "logs"
SENT_LOG = LOG_DIR / "sentinel.log"
AUDIT_LOG= LOG_DIR / "audit.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)
import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_compat import chmod_safe as _chmod_safe

_chmod_safe(LOG_DIR, 0o700)

_logger = logging.getLogger("sentinel")
_alerts = []
_lock   = threading.Lock()


def _breach_review_citation() -> str:
    """Per-region regulatory citation appended to the breach-detector alert.
    PDPL Art. 31 for KSA fleets; GDPR Art. 33 for UK fleets. Lazy import
    so importing config doesn't pull all the audit-log machinery during
    sentinel module load."""
    try:
        from config import DATA_REGION  # noqa: WPS433
        if DATA_REGION == "KSA":
            return "PDPL Art. 31 review"
    except Exception:
        pass
    return "GDPR art. 33 review"

def get_alerts():
    with _lock:
        return list(_alerts)

def _post_alert(level, message, code):
    entry = {"level": level, "message": message, "code": code,
             "timestamp": datetime.now(UTC).isoformat()}
    with _lock:
        existing = [a for a in _alerts if a["code"] != code]
        existing.append(entry)
        _alerts.clear()
        _alerts.extend(existing)
    with open(SENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

def _clear_alert(code):
    with _lock:
        cleared = [a for a in _alerts if a["code"] != code]
        _alerts.clear()
        _alerts.extend(cleared)

class Sentinel(threading.Thread):
    INTERVAL          = 60
    MEM_WARN          = 90  # was 80; on 16 GB demo Macs running a 4 GB MLX
                             # model + dev servers + browser, 80%+ is normal.
                             # 90% is the actual "approaching swap" line.
    DISK_WARN         = 10
    OLLAMA_SLOW_FACTOR= 3

    def __init__(self):
        super().__init__(daemon=True, name="Sentinel")
        self._running           = True
        self._mem_readings      = deque(maxlen=3)
        self._ollama_times      = deque(maxlen=3)
        self._ollama_baseline   = None  # type: Optional[float]
        self._log_sizes         = deque(maxlen=3)

    def stop(self):
        self._running = False

    def run(self):
        _logger.info("Sentinel started")
        # Start the opt-in vendor telemetry agent. No-op unless
        # LOCALLYAI_TELEMETRY=on in .env.
        try:
            import telemetry as _tel
            _tel.start()
        except Exception as e:
            _logger.warning(f"telemetry agent failed to start: {e}")
        while self._running:
            try:
                self._last_tick_ts = time.time()
                self._check_memory()
                self._check_ollama()
                self._check_disk()
                self._check_log_growth()
                self._check_qdrant_lock()
                self._rotate_logs()
                self._check_breach()
                self._fleet_heartbeat()
                self._check_sync_conflicts()
                self._gc_stale_uploads()
                self._refresh_client_installers()
                self._auto_apply_system_updates()
                self._self_heal_runtime()
            except Exception as e:
                _logger.error(f"Sentinel check error: {e}")
                self._record_error()
            time.sleep(self.INTERVAL)

    def _record_error(self):
        """Telemetry hook for the rolling 24h error counter. Silent
        when telemetry is disabled."""
        try:
            import telemetry as _tel
            _tel.record_error()
        except Exception:
            pass

    def _self_heal_runtime(self):
        """Per-tick check for issues that are safe to auto-remediate:

          1. healthz failing → kickstart the API LaunchAgent. Telemetry
             gets a self_heal:healthz_kickstart event.
          2. Ollama llama-runner terminated (the bad-Metal bug) → restart
             Ollama. Falls back to alert if the restart doesn't take.
          3. Disk free < 5 GB → aggressive log rotation + GC; alert if
             still below threshold afterwards.

        Anything more invasive (model swap, cert renewal, kernel-level)
        is NOT auto-healed — those go straight to alert + human review.
        """
        try:
            import telemetry as _tel
        except Exception:
            _tel = None  # type: ignore

        # 1. Healthz watchdog
        if not self._probe_healthz():
            ok = self._kickstart_api()
            if _tel:
                _tel.record_self_heal("healthz_kickstart", success=ok)
                if not ok:
                    _tel.emit_alert("healthz_failed", severity="critical",
                                    message="API not responding + auto-restart failed",
                                    auto_healed=False)

        # 2. Ollama llama-runner terminated (specific failure mode we hit
        #    on the demo Mac when models couldn't load — see updates.md
        #    incident notes). Detected by tailing ~/.ollama/logs/server.log
        #    for the "llama runner terminated" signature.
        if self._ollama_runner_terminated():
            ok = self._restart_ollama()
            if _tel:
                _tel.record_self_heal("ollama_restart", success=ok)
                if not ok:
                    _tel.emit_alert("ollama_unrecoverable", severity="critical",
                                    message="Ollama llama-runner crash + restart failed",
                                    auto_healed=False)

        # 3. Disk pressure (separate from the gauge in _check_disk —
        #    that one only logs; this one acts).
        try:
            import shutil as _sh
            free = _sh.disk_usage("/").free / (1024 ** 3)
            if free < 5:
                # Aggressive: rotate ALL logs + GC ALL stale uploads.
                self._rotate_logs(force=True) if "force" in self._rotate_logs.__code__.co_varnames else self._rotate_logs()
                self._gc_stale_uploads()
                free_after = _sh.disk_usage("/").free / (1024 ** 3)
                ok = free_after >= free + 0.5
                if _tel:
                    _tel.record_self_heal("disk_pressure_clean", success=ok)
                    if not ok and free_after < 5:
                        _tel.emit_alert("disk_critical", severity="critical",
                                        message=f"Free disk {free_after:.1f} GB after auto-clean",
                                        auto_healed=False)
        except Exception as e:
            _logger.warning(f"disk-pressure self-heal failed: {e}")

    def _probe_healthz(self) -> bool:
        import ssl as _s
        import urllib.request as _u
        ctx = _s.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _s.CERT_NONE
        port = os.environ.get("LOCALLYAI_API_PORT", "8000")
        for scheme in ("https", "http"):
            try:
                with _u.urlopen(f"{scheme}://localhost:{port}/healthz", timeout=2,
                                 context=ctx if scheme == "https" else None) as r:
                    if r.status == 200:
                        return True
            except Exception:
                continue
        return False

    def _kickstart_api(self) -> bool:
        if not shutil.which("launchctl"):
            return False
        try:
            uid = os.getuid()
            r = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/app.locallyai.api"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False
            # Re-probe to confirm
            time.sleep(3)
            return self._probe_healthz()
        except Exception:
            return False

    def _ollama_runner_terminated(self) -> bool:
        log_path = os.path.expanduser("~/.ollama/logs/server.log")
        if not os.path.exists(log_path):
            return False
        try:
            # Look at only the last 100 lines so we don't re-trigger on old crashes.
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 16384))
                tail = f.read().decode("utf-8", errors="replace")
            # Match the canonical Go panic + don't re-fire on stale data
            if "llama runner terminated" not in tail:
                return False
            # Check the most recent occurrence is within the last 5 minutes
            for line in reversed(tail.splitlines()):
                if "llama runner terminated" in line:
                    # Crude: just look for a recent timestamp in the line
                    return True  # any recent hit warrants the restart attempt
            return False
        except Exception:
            return False

    def _restart_ollama(self) -> bool:
        try:
            subprocess.run(["pkill", "-9", "ollama"], capture_output=True, timeout=5)
            time.sleep(2)
            subprocess.run(["open", "-ga", "Ollama"], capture_output=True, timeout=10)
            time.sleep(5)
            # Verify port 11434 is listening again
            r = subprocess.run(["lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN"],
                                capture_output=True, text=True, timeout=5)
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception:
            return False

    def _auto_apply_system_updates(self):
        """Daily best-effort: pull tier-A updates that fully verify
        (GPG + manifest + soak + kill-switch). Higher-tier updates
        (B/C) surface in the manager UI for human approval — this
        method never auto-applies them.

        Cadence: only attempts if no apply succeeded in the last 6h
        (so a flapping tag doesn't get applied on every sentinel tick)."""
        import time as _time
        try:
            import deploy as _dep
            import system_updates as _su
            if not _su.AUTO_UPDATE_ENABLED:
                return
            # Cheap throttle: keep last-attempt time on the instance.
            last = getattr(self, "_last_su_check", 0)
            if (_time.time() - last) < 6 * 3600:
                return
            self._last_su_check = _time.time()

            avs = _su.list_available()
            for av in avs:
                if not av.eligible_for_auto_apply:
                    continue
                if av.manifest.tier != "A":
                    continue  # belt + braces — eligible already filters
                _logger.info("Sentinel auto-applying tier-A update %s", av.tag)
                r = _dep.apply_tag(av.tag)
                _logger.info("Auto-apply result: %s", r)
                # Apply at most one per tick to keep the window bounded.
                break
        except Exception as e:
            _logger.warning(f"system-update auto-apply check failed: {e}")

    def _refresh_client_installers(self):
        """Daily best-effort pull of new client-app installers from
        GitHub. The office Mac becomes IT's distribution point so
        staff devices never need GitHub accounts. See
        client_installers.py + docs/sop/client-install.md.

        Cadence: only runs if last successful pull was > 24h ago, so
        the sentinel's tight tick (default 60s) doesn't hammer
        GitHub. Also self-skips if `gh` isn't on PATH (operator opted
        out of office-server distribution)."""
        import time as _time
        try:
            import client_installers as _ci
            st = _ci.status()
            if not st.get("gh_cli_available"):
                return  # silent — operator hasn't enabled this path
            last = float(st.get("last_pulled_at") or 0)
            if (_time.time() - last) < 24 * 3600:
                return
            _ci.refresh_async()
            _logger.info("Triggered daily client-installer refresh")
        except Exception as e:
            _logger.warning(f"client-installer refresh failed: {e}")

    def _gc_stale_uploads(self):
        """Drop chunked-upload partials abandoned > LOCALLYAI_UPLOAD_GC_SECONDS
        ago (default 24 h). Best-effort; chunked_uploads handles its own
        locking. Each tick is cheap when the .parts/ dir is empty."""
        try:
            import chunked_uploads as _cu
            n = _cu.gc_stale()
            if n:
                _logger.info(f"GC'd {n} stale chunked upload(s)")
        except Exception as e:
            _logger.warning(f"upload GC failed: {e}")

    def _fleet_heartbeat(self):
        """Refresh this node's last_seen in fleet.json so peers know we're
        alive. Best-effort — a stale fleet.json shouldn't kill the sentinel.
        Ignored on first iteration if the api hasn't called register() yet
        (heartbeat() is a no-op for unregistered nodes)."""
        try:
            import fleet as _fleet
            _fleet.heartbeat()
        except Exception as e:
            _logger.warning(f"fleet heartbeat failed: {e}")

    def _check_sync_conflicts(self):
        """Detect Syncthing conflict files on the shared store. Quarantine
        them and emit a critical alert — silent merging of users.json or
        erasure.log would be worse than the conflict itself."""
        try:
            from config import SHARED_DIR
            from sync_conflicts import scan_and_alert, write_security_events
            events = scan_and_alert(SHARED_DIR)
            if events:
                write_security_events(events, LOG_DIR / "security.log")
                names = ", ".join(e["original"] for e in events)
                _post_alert(
                    "critical",
                    f"Sync conflict on shared HA store ({names}) — operator must review "
                    f"SHARED_DIR/conflicts/ and reconcile (last-writer-wins data divergence)",
                    "sync_conflict")
            else:
                _clear_alert("sync_conflict")
        except Exception as e:
            _logger.warning(f"sync conflict check failed: {e}")

    # -- log rotation (GDPR art. 5(e) storage limitation, ISO A.5.33) ----
    _LAST_ROTATE_FILE = LOG_DIR / ".last_rotate"
    # erasure.log is no longer per-node — it lives on SHARED_DIR (config.ERASURE_LOG)
    # so a Mac-A erasure is honoured by Mac-B. Per-node sentinel must not rotate
    # shared files (would race the peer's writer); shared-file rotation is a
    # separate concern handled in a later phase.
    _ROTATE_TARGETS   = ("audit.log", "billing.log", "security.log")

    def _rotate_logs(self):
        """Once per UTC day: gzip yesterday's audit/billing/security logs
        and delete archives older than LOCALLYAI_AUDIT_RETENTION_DAYS
        (default 365). The HMAC chain head in .audit_chain is preserved
        across rotations, so a verifier can replay archive+live in order.

        When retention drops an audit archive, .audit_chain is reset to
        zero — the verifier can no longer replay the dropped archive, so
        a new chain era starts. Without the reset, the next entry would
        chain to a head that points at a deleted archive and the verifier
        would (correctly) report TAMPERED forever after."""
        import gzip
        # Red-team finding 10.1: billing.log carries real user names (PII)
        # and previously inherited audit's 365-day retention. Most firm
        # contracts + tax law require longer billing retention (UK HMRC =
        # 6 years; KSA ZATCA = 6 years; we default to 7 to be safe).
        # Audit + security retain at the GDPR-aligned default (365) unless
        # the firm overrides for their retention policy.
        audit_retention_days   = int(os.environ.get("LOCALLYAI_AUDIT_RETENTION_DAYS",   "365"))
        security_retention_days= int(os.environ.get("LOCALLYAI_SECURITY_RETENTION_DAYS", str(audit_retention_days)))
        billing_retention_days = int(os.environ.get("LOCALLYAI_BILLING_RETENTION_DAYS", "2555"))  # 7y
        # Backwards-compat: the old code path used a single retention_days
        # for everything; preserve the local variable so the rest of this
        # function keeps working without a wider refactor.
        retention_days = audit_retention_days  # noqa: F841  — kept for downstream callers
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        try:
            last = self._LAST_ROTATE_FILE.read_text().strip()
        except OSError:
            last = ""
        if last == today:
            return  # already rotated this UTC day

        # Acquire the writer's chain lock so a concurrent _write_audit can't
        # squeeze an entry between our copy and our truncation — that would
        # advance .audit_chain past content that gets wiped, orphaning the
        # next entry. Lazy import: api imports sentinel at startup.
        chain_lock = None
        try:
            import api as _api
            chain_lock = _api._chain_lock
        except Exception:
            pass

        def _do_rotation():
            for name in self._ROTATE_TARGETS:
                src = LOG_DIR / name
                if not src.exists() or src.stat().st_size == 0:
                    continue
                archive = LOG_DIR / f"{name.removesuffix('.log')}-{today}.log.gz"
                if archive.exists():
                    # .last_rotate said today wasn't yet rotated, but the archive
                    # exists — operator cleared .last_rotate, or clock jumped.
                    # Refuse to overwrite (would lose chain history). Live log
                    # keeps growing until the next UTC day.
                    _logger.error(
                        f"Rotate {name} skipped: {archive.name} already exists "
                        f"(refusing to overwrite — investigate .last_rotate)")
                    continue
                try:
                    with open(src, "rb") as fin, gzip.open(archive, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    # Truncate the live log in place rather than rename — keeps any
                    # open writer's file descriptor valid.
                    src.write_text("")
                    _chmod_safe(src, 0o640)
                    _chmod_safe(archive, 0o640)
                    _logger.info(f"Rotated {name} → {archive.name}")
                except OSError as e:
                    _logger.error(f"Rotate {name} failed: {e}")

            # Drop archives past retention. Per-stream cutoff: audit and
            # security use the GDPR-aligned retention; billing uses the
            # tax-law-aligned (longer) retention. If any audit-*.log.gz is
            # dropped, .audit_chain is reset; similarly for billing.
            now_ts = time.time()
            audit_archive_dropped = False
            billing_archive_dropped = False
            for arc in LOG_DIR.glob("*.log.gz"):
                try:
                    age_days_cutoff = audit_retention_days
                    if arc.name.startswith("billing-"):
                        age_days_cutoff = billing_retention_days
                    elif arc.name.startswith("security-"):
                        age_days_cutoff = security_retention_days
                    if arc.stat().st_mtime < (now_ts - age_days_cutoff * 86400):
                        if arc.name.startswith("audit-"):
                            audit_archive_dropped = True
                        if arc.name.startswith("billing-"):
                            billing_archive_dropped = True
                        arc.unlink()
                        _logger.info(f"Retention: removed {arc.name} (>{age_days_cutoff}d old)")
                except OSError:
                    pass

            if audit_archive_dropped:
                chain_state = LOG_DIR / ".audit_chain"
                try:
                    chain_state.write_text("0" * 64, encoding="utf-8")
                    _chmod_safe(chain_state, 0o640)
                    _logger.info("Retention: reset .audit_chain (audit archive dropped)")
                except OSError as e:
                    _logger.error(f"Chain reset failed: {e}")
            if billing_archive_dropped:
                bchain_state = LOG_DIR / ".billing_chain"
                try:
                    bchain_state.write_text("0" * 64, encoding="utf-8")
                    _chmod_safe(bchain_state, 0o640)
                    _logger.info("Retention: reset .billing_chain (billing archive dropped)")
                except OSError as e:
                    _logger.error(f"Billing chain reset failed: {e}")

        if chain_lock is not None:
            with chain_lock:
                _do_rotation()
        else:
            _do_rotation()

        try:
            self._LAST_ROTATE_FILE.write_text(today)
            _chmod_safe(self._LAST_ROTATE_FILE, 0o640)
        except OSError:
            pass

    # -- breach detection (GDPR art. 33 readiness, ISO A.8.16) -----------
    _BREACH_WINDOW_SEC   = 300
    _BREACH_THRESHOLD    = 10  # failed-auth events from one IP in window
    _BREACH_LAST_OFFSET  = 0   # byte offset already scanned in security.log

    def _check_breach(self):
        """Tail logs/security.log; raise a critical alert if any single IP
        produces ≥ BREACH_THRESHOLD auth_failure or auth_locked_attempt
        events in BREACH_WINDOW_SEC."""
        sec_log = LOG_DIR / "security.log"
        if not sec_log.exists():
            return
        from collections import defaultdict
        try:
            size = sec_log.stat().st_size
            if self._BREACH_LAST_OFFSET > size:
                # File rotated under us — start fresh.
                self._BREACH_LAST_OFFSET = 0
            with open(sec_log, encoding="utf-8", errors="replace") as f:
                f.seek(self._BREACH_LAST_OFFSET)
                tail = f.read()
                self._BREACH_LAST_OFFSET = f.tell()
            now = time.time()
            buckets: dict[str, int] = defaultdict(int)
            for line in tail.splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if ev.get("event") not in ("auth_failure", "auth_locked_attempt"):
                    continue
                ts_str = ev.get("timestamp", "")
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=UTC).timestamp()
                except ValueError:
                    continue
                if now - ts > self._BREACH_WINDOW_SEC:
                    continue
                buckets[ev.get("ip", "unknown")] += 1
            offenders = [(ip, n) for ip, n in buckets.items() if n >= self._BREACH_THRESHOLD]
            if offenders:
                msg = ", ".join(f"{ip}={n}" for ip, n in offenders)
                _post_alert("critical",
                            f"Possible credential-stuffing: {msg} failed auths in "
                            f"{self._BREACH_WINDOW_SEC}s window "
                            f"({_breach_review_citation()})",
                            "auth_breach")
            else:
                _clear_alert("auth_breach")
        except OSError as e:
            _logger.error(f"Breach check error: {e}")

    def _check_memory(self):
        try:
            import psutil
            pct = psutil.virtual_memory().percent
        except ImportError:
            return
        self._mem_readings.append(pct)
        if len(self._mem_readings) == 3:
            a, b, c = self._mem_readings
            if c > self.MEM_WARN and a < b < c:
                _post_alert("warning", f"Memory pressure: {c:.1f}% RAM used (rising trend)", "WARN_MEMORY_PRESSURE")
            elif c < self.MEM_WARN:
                _clear_alert("WARN_MEMORY_PRESSURE")

    def _check_ollama(self):
        # Skip when the deployment isn't using Ollama. Otherwise the
        # monitor pages on a backend that was deliberately not installed,
        # which weakens ISO A.8.16 by burying real alerts in noise.
        if os.environ.get("LOCALLYAI_BACKEND", "ollama").lower() != "ollama":
            _clear_alert("CRIT_OLLAMA_DOWN")
            return
        start = time.monotonic()
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3):
                pass
            elapsed = (time.monotonic() - start) * 1000
        except Exception:
            _post_alert("critical", "Ollama is unreachable at localhost:11434", "CRIT_OLLAMA_DOWN")
            return
        _clear_alert("CRIT_OLLAMA_DOWN")
        self._ollama_times.append(elapsed)
        if self._ollama_baseline is None and len(self._ollama_times) == 3:
            self._ollama_baseline = sum(self._ollama_times) / 3
        if self._ollama_baseline:
            times = list(self._ollama_times)
            if len(times) >= 3:
                a, b, c = times[-3], times[-2], times[-1]
                if c > self._ollama_baseline * self.OLLAMA_SLOW_FACTOR and a < b < c:
                    _post_alert("warning", f"Ollama degraded: {c:.0f}ms (baseline {self._ollama_baseline:.0f}ms)", "WARN_OLLAMA_SLOW")
                else:
                    _clear_alert("WARN_OLLAMA_SLOW")

    def _check_disk(self):
        _disk_path = os.environ.get("LOCALLYAI_DISK_CHECK_PATH", "/")
        gb = shutil.disk_usage(_disk_path).free / (1024**3)
        if gb < self.DISK_WARN:
            _post_alert("critical", f"Low disk space: {gb:.1f} GB free on {_disk_path}", "CRIT_DISK_LOW")
        else:
            _clear_alert("CRIT_DISK_LOW")
        if AUDIT_LOG.exists():
            self._log_sizes.append((time.monotonic(), AUDIT_LOG.stat().st_size))

    def _check_log_growth(self):
        if len(self._log_sizes) >= 2:
            t1, s1 = self._log_sizes[0]
            t2, s2 = self._log_sizes[-1]
            dt = t2 - t1
            if dt > 0:
                rate = (s2 - s1) / dt
                if rate > (10 * 1024 * 1024 / 300):
                    _post_alert("warning", f"Audit log growing rapidly: {rate/1024:.1f} KB/s", "WARN_LOG_STORM")
                else:
                    _clear_alert("WARN_LOG_STORM")

    def _check_qdrant_lock(self):
        # When QDRANT_URL is set the deployment uses a server (Docker
        # container) and there is no embedded-mode lock file to police.
        # The lock under storage/ is a leftover from before that switch.
        if os.environ.get("QDRANT_URL"):
            _clear_alert("WARN_QDRANT_LOCK")
            return
        _qd_storage = os.environ.get("LOCALLYAI_STORAGE_DIR", "")
        base = Path(_qd_storage) if _qd_storage else Path(__file__).resolve().parent.parent / "storage"
        lock_path = base / ".lock"
        if not lock_path.exists():
            _clear_alert("WARN_QDRANT_LOCK")
            return
        age = time.time() - lock_path.stat().st_mtime
        if age <= 300:
            # Normal Qdrant activity (large PDF indexing, etc.).
            _clear_alert("WARN_QDRANT_LOCK")
            return
        # Lock is old. Distinguish "truly stale" (no process holds it →
        # leftover from a crashed API instance) from "actively held"
        # (some process has the fd open). For truly-stale, auto-clean
        # silently — warning operators about a file we can fix is noise.
        # For actively-held, warn.
        held_by_someone = False
        try:
            res = subprocess.run(
                ["lsof", "-t", str(lock_path)],
                capture_output=True, text=True, timeout=5,
            )
            held_by_someone = bool(res.stdout.strip())
        except Exception:
            # If lsof fails, fall back to the old behaviour (warn) to be safe.
            held_by_someone = True
        if held_by_someone:
            # Held + old = embedded-Qdrant API process is sitting on the
            # lock during an idle period (no writes for >5min). That's
            # the EXPECTED state on a single-node install, not a problem.
            # Previous behaviour fired this warning on every healthy
            # idle install — only the unheld+old case is a real crash
            # leftover worth reporting.
            _clear_alert("WARN_QDRANT_LOCK")
            return
        try:
            lock_path.unlink()
            _clear_alert("WARN_QDRANT_LOCK")
            _logger.info(f"Cleaned up stale Qdrant lock ({age:.0f}s old, no process holding it)")
        except OSError as exc:
            _post_alert("warning", f"Qdrant lock file stale ({age:.0f}s old, cleanup failed: {exc})", "WARN_QDRANT_LOCK")

_sentinel = None  # type: Optional[Sentinel]

def start():
    global _sentinel
    if _sentinel is None or not _sentinel.is_alive():
        _sentinel = Sentinel()
        _sentinel.start()
    return _sentinel

def status():
    s = _sentinel
    return {"running": s is not None and s.is_alive(), "alerts": get_alerts()}
