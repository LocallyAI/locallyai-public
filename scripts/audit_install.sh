#!/usr/bin/env bash
# audit_install.sh — health audit for a deployed LocallyAI Mac Studio.
# Runs locally (loopback access required). Suitable for launchd weekly jobs.
#
# Exit codes:
#   0 — all 8 checks pass
#   1 — at least one check failed (launchd surfaces this)
#
# Output: production/logs/install_audit_YYYY-MM-DD.log (Markdown)
# If LOCALLYAI_ALERT_WEBHOOK_URL is set, failures are POSTed as JSON.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${LOCALLYAI_ENV_FILE:-$DIR/.env}"
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"

DATE=$(date -u +%Y-%m-%d)
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
REPORT="$LOG_DIR/install_audit_$DATE.log"

# Load .env without polluting the shell beyond the keys we need.
if [[ -f "$ENV_FILE" ]]; then
  ADMIN_KEY=$(awk -F= '/^LOCALLYAI_ADMIN_KEY=/ {print $2}' "$ENV_FILE")
  API_BASE=$(awk -F= '/^LOCALLYAI_API_BASE=/ {print $2}' "$ENV_FILE")
  WEBHOOK=$(awk -F= '/^LOCALLYAI_ALERT_WEBHOOK_URL=/ {print $2}' "$ENV_FILE")
else
  ADMIN_KEY="" ; API_BASE="https://localhost:8000" ; WEBHOOK=""
fi
API_BASE="${API_BASE:-https://localhost:8000}"
# TLS state in tls/ wins over a stale http:// in .env: install.sh §8b
# always generates an HTTPS cert when openssl is present, but older .env
# files still carry the http:// default. Probe what's actually bound.
if [[ -f "$DIR/tls/cert.pem" && "$API_BASE" =~ ^http:// ]]; then
  API_BASE="https://${API_BASE#http://}"
fi

PASS=0 ; WARN=0 ; FAIL=0
declare -a FAILURES=()

emit() {
  local status="$1" name="$2" detail="$3"
  case "$status" in
    pass) PASS=$((PASS+1)) ;;
    warn) WARN=$((WARN+1)) ;;
    fail) FAIL=$((FAIL+1)) ; FAILURES+=("$name: $detail") ;;
  esac
  printf '## %s\nSTATUS: %s\nDetail: %s\n\n' "$name" "$status" "$detail" >> "$REPORT"
  printf '[%s] %s — %s\n' "$status" "$name" "$detail"
}

{ echo "# LocallyAI install audit — $TS"; echo ""; } > "$REPORT"

# 1. launchd service alive
if launchctl list 2>/dev/null | grep -q com.locallyai.server; then
  PID=$(launchctl list 2>/dev/null | awk '/com\.locallyai\.server/ {print $1}')
  emit pass "1. launchd service" "com.locallyai.server PID=$PID"
else
  emit fail "1. launchd service" "com.locallyai.server not loaded — run: launchctl load ~/Library/LaunchAgents/com.locallyai.server.plist"
fi

# 2. /healthz
HZ=$(curl -sfk "$API_BASE/healthz" 2>/dev/null || echo "")
if [[ -n "$HZ" ]]; then
  emit pass "2. /healthz" "$HZ"
else
  emit fail "2. /healthz" "no response from $API_BASE/healthz"
fi

# 3. /admin/audit-verify
if [[ -z "$ADMIN_KEY" ]]; then
  emit warn "3. audit chain" "LOCALLYAI_ADMIN_KEY not set in $ENV_FILE — cannot verify"
else
  AV=$(curl -sfk -H "Authorization: Bearer $ADMIN_KEY" "$API_BASE/admin/audit-verify" 2>/dev/null || echo "")
  if echo "$AV" | grep -q '"status":"ok"'; then
    emit pass "3. audit chain" "$AV"
  elif echo "$AV" | grep -q '"status":"skipped"'; then
    emit warn "3. audit chain" "HMAC chain disabled — set LOCALLYAI_AUDIT_HMAC_KEY"
  else
    emit fail "3. audit chain" "${AV:-no response}"
  fi
fi

# 4. /monitor/alerts
if [[ -n "$ADMIN_KEY" ]]; then
  AL=$(curl -sfk -H "Authorization: Bearer $ADMIN_KEY" "$API_BASE/monitor/alerts" 2>/dev/null || echo "")
  if echo "$AL" | grep -q '"status":"ok"'; then
    emit pass "4. monitor alerts" "all green"
  elif echo "$AL" | grep -q '"status":"degraded"'; then
    emit warn "4. monitor alerts" "$AL"
  else
    emit fail "4. monitor alerts" "${AL:-no response}"
  fi
fi

# 5. Heartbeat & resurrector log tail
HB_LOG="$LOG_DIR/heartbeat.log"
RS_LOG="$LOG_DIR/resurrector.log"
if [[ -f "$HB_LOG" ]]; then
  RECENT_FAIL=$(tail -n 50 "$HB_LOG" | grep -c '"event": "probe_failed"' || true)
  RESURRECT=$(tail -n 50 "$HB_LOG" | grep -c '"event": "resurrector_triggered"' || true)
  if [[ "$RECENT_FAIL" -gt 5 || "$RESURRECT" -gt 0 ]]; then
    emit warn "5. heartbeat tail" "$RECENT_FAIL probe_failed and $RESURRECT resurrector_triggered in last 50 lines"
  else
    emit pass "5. heartbeat tail" "$RECENT_FAIL probe_failed in last 50 lines"
  fi
else
  emit warn "5. heartbeat tail" "$HB_LOG not present"
fi
if [[ -f "$RS_LOG" ]]; then
  RS_LAST=$(tail -n 20 "$RS_LOG" | tail -n 1 || true)
  emit pass "5b. resurrector tail" "${RS_LAST:-empty}"
fi

# 6. Ollama models
if command -v ollama >/dev/null 2>&1; then
  OL=$(ollama list 2>/dev/null || echo "")
  HAS_LLM=0 ; HAS_EMBED=0
  echo "$OL" | grep -q 'qwen2.5'           && HAS_LLM=1
  echo "$OL" | grep -q 'nomic-embed-text'  && HAS_EMBED=1
  if [[ $HAS_LLM -eq 1 && $HAS_EMBED -eq 1 ]]; then
    emit pass "6. ollama models" "qwen2.5 and nomic-embed-text both present"
  else
    emit fail "6. ollama models" "missing — LLM=$HAS_LLM EMBED=$HAS_EMBED"
  fi
else
  emit fail "6. ollama models" "ollama CLI not in PATH"
fi

# 7. users.json populated
USERS_FILE="$DIR/users.json"
if [[ -f "$USERS_FILE" ]] && [[ -s "$USERS_FILE" ]]; then
  COUNT=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$USERS_FILE" 2>/dev/null || echo 0)
  if [[ "$COUNT" =~ ^[0-9]+$ ]] && [[ "$COUNT" -gt 0 ]]; then
    emit pass "7. users.json" "$COUNT user(s) configured"
  else
    emit fail "7. users.json" "file present but empty or unparseable"
  fi
else
  emit fail "7. users.json" "missing — run: python manage_users.py add <Name>"
fi

# 8. Compliance / hardening checks (GDPR art. 32, ISO 27001 A.8.3 + A.8.24).
#    These don't fail the audit individually unless they're severe — but
#    each becomes a row in the report so an operator can see drift.

# 8a. FileVault on (protects tls/key.pem and audit.log at rest)
if command -v fdesetup >/dev/null 2>&1; then
  FV=$(fdesetup status 2>/dev/null | head -n 1 || true)
  case "$FV" in
    *"FileVault is On"*) emit pass "8a. FileVault" "On — disk encryption protects tls/key.pem and logs at rest" ;;
    *"FileVault is Off"*) emit fail "8a. FileVault" "Off — at-rest data is not encrypted; enable in System Settings → Privacy & Security" ;;
    *) emit warn "8a. FileVault" "${FV:-unknown}" ;;
  esac
fi

# 8b. tls/key.pem is owner-only
KEY_FILE="$DIR/tls/key.pem"
if [[ -f "$KEY_FILE" ]]; then
  PERMS=$(stat -f '%Lp' "$KEY_FILE" 2>/dev/null || stat -c '%a' "$KEY_FILE" 2>/dev/null || echo "")
  if [[ "$PERMS" == "600" ]]; then
    emit pass "8b. tls/key.pem perms" "0$PERMS"
  else
    emit fail "8b. tls/key.pem perms" "expected 600, got 0$PERMS — chmod 600 $KEY_FILE"
  fi
fi

# 8c. audit.log + billing.log not world-readable
for f in audit.log billing.log; do
  LF="$DIR/logs/$f"
  if [[ -f "$LF" ]]; then
    PERMS=$(stat -f '%Lp' "$LF" 2>/dev/null || stat -c '%a' "$LF" 2>/dev/null || echo "")
    # Bash arithmetic uses base-prefix syntax (8#nnn) for octal AND/OR.
    OTHER=$(( 8#${PERMS} & 8#7 ))
    if [[ "$OTHER" == "0" ]]; then
      emit pass "8c. logs/$f perms" "0$PERMS — others have no access"
    else
      emit fail "8c. logs/$f perms" "0$PERMS lets 'other' read; chmod 640 $LF"
    fi
  fi
done

# 8d. data/ directory is gitignored (prevents client docs leaking on push)
if [[ -d "$DIR/.git" ]]; then
  if ( cd "$DIR" && git check-ignore -q data/ ) 2>/dev/null; then
    emit pass "8d. data/ gitignore" "data/ ignored — client documents won't be committed"
  else
    TRACKED=$( cd "$DIR" && git ls-files data/ 2>/dev/null | head -n 1 || true )
    if [[ -n "$TRACKED" ]]; then
      emit fail "8d. data/ gitignore" "data/ is tracked in git! Add 'data/' to .gitignore and 'git rm --cached -r data/'"
    else
      emit warn "8d. data/ gitignore" "data/ not explicitly ignored; verify .gitignore before publishing"
    fi
  fi
fi

# 8e. .env not world-readable (contains LOCALLYAI_ADMIN_KEY)
if [[ -f "$ENV_FILE" ]]; then
  PERMS=$(stat -f '%Lp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE" 2>/dev/null || echo "")
  if [[ "$PERMS" == "600" ]]; then
    emit pass "8e. .env perms" "0$PERMS"
  else
    emit fail "8e. .env perms" "expected 600, got 0$PERMS — chmod 600 $ENV_FILE"
  fi
fi

# 9. Summary + alert webhook
{
  echo "# Summary"
  echo ""
  echo "- Pass: $PASS"
  echo "- Warn: $WARN"
  echo "- Fail: $FAIL"
} >> "$REPORT"

echo ""
echo "─────────────────────────────────────"
echo " Audit complete: pass=$PASS warn=$WARN fail=$FAIL"
echo " Report: $REPORT"
echo "─────────────────────────────────────"

if [[ $FAIL -gt 0 && -n "$WEBHOOK" ]]; then
  PAYLOAD=$(printf '{"deployment":"locallyai","timestamp":"%s","fail":%d,"warn":%d,"failures":"%s"}' \
    "$TS" "$FAIL" "$WARN" "$(printf '%s\n' "${FAILURES[@]}" | sed 's/"/\\"/g' | tr '\n' '|')")
  curl -fsS -X POST -H 'Content-Type: application/json' -d "$PAYLOAD" "$WEBHOOK" >/dev/null 2>&1 \
    && echo "Alert posted to webhook" \
    || echo "WARN: webhook post failed"
fi

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
