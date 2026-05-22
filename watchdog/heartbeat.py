# Agent 2: Heartbeat -- probes API health, triggers Resurrector after 3 failures
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR   = Path(__file__).resolve().parent.parent
LOG_DIR    = BASE_DIR / 'logs'
HB_LOG     = LOG_DIR / 'heartbeat.log'
LOG_DIR.mkdir(parents=True, exist_ok=True)

API_BASE     = os.environ.get('LOCALLYAI_API_BASE', 'http://localhost:8000')
ADMIN_KEY    = os.environ.get('LOCALLYAI_ADMIN_KEY', '')
INTERVAL     = int(os.environ.get('HB_INTERVAL', '30'))
MAX_FAILURES = int(os.environ.get('HB_MAX_FAILURES', '3'))
TIMEOUT      = int(os.environ.get('HB_TIMEOUT', '10'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [HEARTBEAT] %(message)s')
log = logging.getLogger('heartbeat')

def _log(event: str, detail: str = ''):
    entry = {'timestamp': datetime.now(UTC).isoformat(),
             'event': event, 'detail': detail}
    with open(HB_LOG, 'a', encoding='utf-8') as f:
        f.write(__import__('json').dumps(entry) + '\n')

import ssl

# The local TLS cert is self-signed by install.sh -- no public CA chain. The
# watchdog probes loopback only, so accepting any cert is correct here.
_PROBE_SSL_CTX = ssl.create_default_context()
_PROBE_SSL_CTX.check_hostname = False
_PROBE_SSL_CTX.verify_mode = ssl.CERT_NONE


def _probe() -> bool:
    # /healthz is unauthenticated by design -- the watchdog should not hold
    # a user API key, and the admin key does not validate against /v1/models.
    try:
        req = urllib.request.Request(f'{API_BASE}/healthz')
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_PROBE_SSL_CTX) as r:
            return r.status == 200
    except Exception:
        return False

def _trigger_resurrector(reason: str):
    resurrector = Path(__file__).resolve().parent / 'resurrector.py'
    try:
        subprocess.Popen(
            [sys.executable, str(resurrector), '--reason', reason],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
        )
        _log('resurrector_triggered', reason)
        log.warning(f'Resurrector triggered: {reason}')
    except Exception as e:
        _log('resurrector_trigger_failed', str(e))
        log.error(f'Failed to trigger resurrector: {e}')

RECOVERY_COOLDOWN = int(os.environ.get('HB_RECOVERY_COOLDOWN', '120'))
# Cold start: MLX + embedding model load + Qdrant collection check can take
# 60–120 s the first time. Probing during that window finds an unbound port
# and used to kill a healthy API mid-load. Don't trigger the resurrector
# until we've seen at least one successful probe OR until WARMUP_GRACE has
# elapsed since heartbeat startup. Tunable via env for slow disks.
WARMUP_GRACE = int(os.environ.get('HB_WARMUP_GRACE', '180'))


def run():
    consecutive_failures = 0
    recovery_until = 0.0
    started_at = time.monotonic()
    has_been_healthy = False  # flips True after the first successful probe
    log.info(f'Heartbeat started. Probing {API_BASE} every {INTERVAL}s, max failures={MAX_FAILURES}, warmup grace {WARMUP_GRACE}s')
    _log('started', f'interval={INTERVAL} max_failures={MAX_FAILURES} timeout={TIMEOUT} grace={WARMUP_GRACE}')

    while True:
        ok = _probe()
        if ok:
            if not has_been_healthy:
                _log('warmup_complete', f'first probe ok after {time.monotonic()-started_at:.0f}s')
            has_been_healthy = True
            if consecutive_failures > 0:
                _log('recovered', f'after {consecutive_failures} failures')
                log.info(f'API recovered after {consecutive_failures} failures')
            consecutive_failures = 0
            recovery_until = 0.0
            _log('ok')
        else:
            consecutive_failures += 1
            _log('probe_failed', f'consecutive={consecutive_failures}')
            log.warning(f'Probe failed ({consecutive_failures}/{MAX_FAILURES})')
            in_warmup = (not has_been_healthy
                         and (time.monotonic() - started_at) < WARMUP_GRACE)
            if consecutive_failures >= MAX_FAILURES:
                if in_warmup:
                    log.info('Within warmup grace; not triggering resurrector')
                    _log('resurrector_skipped', f'warmup grace {WARMUP_GRACE}s')
                    consecutive_failures = 0
                elif time.monotonic() < recovery_until:
                    log.info('Resurrector already running — skipping re-trigger')
                    _log('resurrector_skipped', 'cooldown active')
                    consecutive_failures = 0
                else:
                    _trigger_resurrector(f'Heartbeat: {consecutive_failures} consecutive probe failures')
                    recovery_until = time.monotonic() + RECOVERY_COOLDOWN
                    consecutive_failures = 0

        time.sleep(INTERVAL)

if __name__ == '__main__':
    run()
