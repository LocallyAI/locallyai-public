"""Plugin loader: parses claude-for-legal-format plugin directories and exposes
them to api/chat.py + two new endpoints (GET /v1/plugins and
GET /v1/plugins/{name}/skill).

Each plugin directory is shaped (mirroring Anthropic's upstream):
    <plugin>/
      .claude-plugin/plugin.json   # metadata (name, version, description, ...)
      CLAUDE.md                    # practice profile (injected into system prompt)
      skills/<skill>/SKILL.md      # markdown template (optional YAML frontmatter)
      .mcp.json                    # OPTIONAL — declares which in-process MCP
                                   #   servers this plugin's skills will call.
                                   #   We read only the top-level `mcpServers`
                                   #   key names; the upstream-style command/args
                                   #   fields are ignored (in-process for week 1).

Plugins are loaded once at startup. A redeploy is required to pick up new
plugin files. The list is small (low dozens of plugins, low dozens of skills
each) so we keep them in memory.

The loader is paranoid about plugin content:
  - Plugin + skill names must match ^[a-z0-9\\-]{1,64}$
  - CLAUDE.md and every SKILL.md body run through sanitize_markdown_body
  - Any plugin containing looks_like_prompt_injection markers is rejected
  - Unknown / malformed plugins are SKIPPED with a logged warning, never raise

Tool-defs are aggregated from the 4 in-process mcp_servers/* modules. A plugin
that declares only `mcp-locallyai-search` in its .mcp.json sees ONLY that
server's TOOL_DEFS; it does not get the full toolbox. This keeps Qwen 2.5's
tool-selection latency manageable on a 7B local model.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from slowapi import Limiter
from slowapi.util import get_remote_address

from api._shared import (
    _admin_auth,
    _auth,
    _write_security_log,
    looks_like_prompt_injection,
    sanitize_markdown_body,
)
from config import SHARED_DIR
from shared_lock import shared_lock

log = logging.getLogger("api")

# ── Configuration ─────────────────────────────────────────────────────────────

_NAME_PATTERN = re.compile(r"^[a-z0-9\-]{1,64}$")
_MAX_SKILL_BODY_BYTES = 8000
_MAX_PRACTICE_PROFILE_BYTES = 8000
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)

# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class SkillSpec:
    name: str
    description: str
    body_md: str
    trigger_phrases: list[str] = field(default_factory=list)


@dataclass
class PluginSpec:
    name: str
    version: str
    description: str
    practice_profile_md: str
    skills: dict[str, SkillSpec]
    declared_mcp_servers: list[str]


# ── Registry (populated by load_plugins_from_dir at startup) ──────────────────

_PLUGIN_REGISTRY: dict[str, PluginSpec] = {}

# ── Enable/disable state (Manager UI marketplace) ────────────────────────────
# Persisted to SHARED_DIR (Syncthing-replicated on multi-Mac fleets) so
# enabling a plugin on Mac A propagates to Mac B without a re-deploy.
# Schema: {"plugins": {"<name>": bool, ...}, "mcp_servers": {"<name>": bool, ...}}
# Missing entries default to ENABLED (a fresh install with no overrides has
# every loaded plugin + every MCP server live — least-surprise for the demo
# path). Disabling writes the file with that entry set to False.
_MARKETPLACE_STATE_FILE = SHARED_DIR / "marketplace_state.json"
_KNOWN_MCP_SERVERS = (
    "mcp-locallyai-search",
    "mcp-locallyai-audit",
    "mcp-locallyai-matter",
    "mcp-locallyai-citation",
)


def _read_marketplace_state() -> dict:
    """Read the marketplace toggle state. Returns the default-empty dict
    if the file doesn't exist or is malformed. Caller does NOT need to
    hold the shared lock for read-only access."""
    if not _MARKETPLACE_STATE_FILE.exists():
        return {"plugins": {}, "mcp_servers": {}}
    try:
        data = json.loads(_MARKETPLACE_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"plugins": {}, "mcp_servers": {}}
        data.setdefault("plugins", {})
        data.setdefault("mcp_servers", {})
        return data
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"plugins: marketplace state unreadable, defaulting to all-enabled: {e}")
        return {"plugins": {}, "mcp_servers": {}}


def _write_marketplace_state(state: dict) -> None:
    """Atomic write through shared_lock so a Syncthing-replicated fleet
    doesn't race on concurrent toggles. tmp + rename."""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    with shared_lock(_MARKETPLACE_STATE_FILE, timeout=5.0):
        tmp = _MARKETPLACE_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(_MARKETPLACE_STATE_FILE)


def is_plugin_enabled(name: str) -> bool:
    """Default-enabled: only False if the marketplace state file explicitly
    sets the plugin to False. Keeps the demo path working out-of-the-box."""
    return _read_marketplace_state()["plugins"].get(name, True)


def is_mcp_enabled(name: str) -> bool:
    """Default-enabled (same reasoning as is_plugin_enabled)."""
    return _read_marketplace_state()["mcp_servers"].get(name, True)


def set_plugin_enabled(name: str, enabled: bool) -> None:
    state = _read_marketplace_state()
    state["plugins"][name] = bool(enabled)
    _write_marketplace_state(state)


def set_mcp_enabled(name: str, enabled: bool) -> None:
    state = _read_marketplace_state()
    state["mcp_servers"][name] = bool(enabled)
    _write_marketplace_state(state)


def _strip_html_comments(text: str) -> str:
    """Remove <!-- ... --> blocks (used by us to annotate cloud-tool
    substitutions in the SKILL.md body without leaking them to the model)."""
    return _HTML_COMMENT_RE.sub("", text)


def _parse_skill_frontmatter(body: str) -> tuple[dict[str, Any], str]:
    """Parse optional YAML-ish front-matter at the top of a SKILL.md.

    Format (claude-for-legal upstream uses this for skill name/description):
        ---
        name: clearance
        description: Run a trademark clearance check
        user-invocable: true
        ---
        <markdown body>

    We avoid the PyYAML dependency by parsing just the simple `key: value`
    shape upstream uses. Lines that don't match are kept verbatim under
    the special key `_raw`.
    """
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return {}, body
    front_text, rest = match.group(1), match.group(2)
    meta: dict[str, Any] = {}
    for line in front_text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.lower() in ("true", "false"):
            meta[key.strip()] = (val.lower() == "true")
        else:
            meta[key.strip()] = val.strip("'\"")
    return meta, rest


def _load_one_plugin(plugin_dir: Path) -> Optional[PluginSpec]:
    """Parse a single plugin directory. Returns None (and logs a warning) on
    any malformation — we never raise out of the loader so a single bad
    plugin can't crash startup."""
    name = plugin_dir.name
    if not _NAME_PATTERN.match(name):
        log.warning(f"plugins: skip '{name}' — name does not match pattern")
        return None

    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest_path.is_file():
        log.warning(f"plugins: skip '{name}' — missing .claude-plugin/plugin.json")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"plugins: skip '{name}' — manifest unreadable: {e}")
        return None

    version = str(manifest.get("version", "0.0.0"))
    description = str(manifest.get("description", "")).strip()

    practice_profile_path = plugin_dir / "CLAUDE.md"
    practice_profile_md = ""
    if practice_profile_path.is_file():
        try:
            raw = practice_profile_path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning(f"plugins: skip '{name}' — CLAUDE.md unreadable: {e}")
            return None
        if looks_like_prompt_injection(raw):
            log.warning(f"plugins: REJECT '{name}' — CLAUDE.md contains injection markers")
            _write_security_log(
                event="PLUGIN_REJECTED",
                ip="",
                detail=f"plugin={name} reason=injection_in_practice_profile",
            )
            return None
        practice_profile_md = sanitize_markdown_body(
            _strip_html_comments(raw), max_len=_MAX_PRACTICE_PROFILE_BYTES,
        )

    skills: dict[str, SkillSpec] = {}
    skills_root = plugin_dir / "skills"
    if skills_root.is_dir():
        for skill_dir in sorted(skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            if not _NAME_PATTERN.match(skill_name):
                log.warning(f"plugins: '{name}' — skip skill '{skill_name}' (bad name)")
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                log.warning(f"plugins: '{name}/{skill_name}' — missing SKILL.md")
                continue
            try:
                raw = skill_md.read_text(encoding="utf-8")
            except OSError as e:
                log.warning(f"plugins: '{name}/{skill_name}' — unreadable: {e}")
                continue
            if looks_like_prompt_injection(raw):
                log.warning(f"plugins: REJECT '{name}' — SKILL.md '{skill_name}' contains injection markers")
                _write_security_log(
                    event="PLUGIN_REJECTED",
                    ip="",
                    detail=f"plugin={name} skill={skill_name} reason=injection_in_skill",
                )
                return None  # reject the whole plugin
            meta, body = _parse_skill_frontmatter(raw)
            body_clean = sanitize_markdown_body(
                _strip_html_comments(body), max_len=_MAX_SKILL_BODY_BYTES,
            )
            skill_desc = str(meta.get("description", "")).strip()
            triggers_raw = meta.get("trigger-phrases", "") or meta.get("argument-hint", "")
            triggers = [t.strip() for t in str(triggers_raw).split(",") if t.strip()]
            skills[skill_name] = SkillSpec(
                name=skill_name,
                description=skill_desc,
                body_md=body_clean,
                trigger_phrases=triggers,
            )

    mcp_path = plugin_dir / ".mcp.json"
    declared_mcp: list[str] = []
    if mcp_path.is_file():
        try:
            mcp_doc = json.loads(mcp_path.read_text(encoding="utf-8"))
            servers = mcp_doc.get("mcpServers", {}) or {}
            if isinstance(servers, dict):
                declared_mcp = sorted(servers.keys())
        except (OSError, json.JSONDecodeError) as e:
            log.warning(f"plugins: '{name}' — .mcp.json unreadable, ignoring: {e}")

    return PluginSpec(
        name=name,
        version=version,
        description=description,
        practice_profile_md=practice_profile_md,
        skills=skills,
        declared_mcp_servers=declared_mcp,
    )


def load_plugins_from_dir(path: Path) -> dict[str, PluginSpec]:
    """Replace the in-memory registry with the contents of `path`. Returns the
    new registry. Called once from api/__init__.py's startup handler; safe
    to call again (idempotent, but pricier than a single load)."""
    global _PLUGIN_REGISTRY
    new_registry: dict[str, PluginSpec] = {}
    if not path.is_dir():
        log.info(f"plugins: directory {path} not present — no plugins loaded")
        _PLUGIN_REGISTRY = {}
        return _PLUGIN_REGISTRY
    for child in sorted(path.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        spec = _load_one_plugin(child)
        if spec is not None:
            new_registry[spec.name] = spec
    _PLUGIN_REGISTRY = new_registry
    log.info(
        f"plugins: loaded {len(new_registry)} plugin(s): "
        f"{', '.join(sorted(new_registry.keys())) or '(none)'}"
    )
    return _PLUGIN_REGISTRY


def get_plugin(name: str) -> Optional[PluginSpec]:
    """Return the plugin spec if loaded AND enabled in the marketplace state.
    Disabled plugins are invisible to the chat handler — same effect as
    not being installed. The Marketplace UI sees them via list_plugins()
    which honours the include_disabled flag."""
    spec = _PLUGIN_REGISTRY.get(name)
    if spec is None:
        return None
    if not is_plugin_enabled(name):
        return None
    return spec


def get_skill(plugin: str, skill: str) -> Optional[SkillSpec]:
    spec = get_plugin(plugin)
    if spec is None:
        return None
    return spec.skills.get(skill)


def list_plugins(include_disabled: bool = False) -> list[dict]:
    """Serialisable view of the registry — used by GET /v1/plugins (chat
    consumers, enabled-only) and the marketplace endpoint
    (include_disabled=True so admins see everything)."""
    out: list[dict] = []
    for spec in sorted(_PLUGIN_REGISTRY.values(), key=lambda s: s.name):
        enabled = is_plugin_enabled(spec.name)
        if not enabled and not include_disabled:
            continue
        out.append({
            "name": spec.name,
            "version": spec.version,
            "description": spec.description,
            "enabled": enabled,
            "skills": [
                {"name": s.name, "description": s.description}
                for s in spec.skills.values()
            ],
            "mcp_servers": spec.declared_mcp_servers,
        })
    return out


# ── Chat-handler integration helpers ──────────────────────────────────────────


def build_chat_system_prompt_addendum(
    plugin: Optional[str],
    skill: Optional[str],
) -> Optional[str]:
    """Return the markdown block to splice into chat.py's base_persona when a
    plugin/skill is active. Order matters: practice profile (role) → skill
    body (task). Retrieval context is appended after this by chat.py."""
    if not plugin:
        return None
    spec = _PLUGIN_REGISTRY.get(plugin)
    if spec is None:
        log.warning(f"plugins: chat requested unknown plugin '{plugin}'")
        return None
    parts: list[str] = []
    if spec.practice_profile_md:
        parts.append(f"# Practice profile: {spec.name}\n\n{spec.practice_profile_md}")
    if skill:
        skill_spec = spec.skills.get(skill)
        if skill_spec is None:
            log.warning(f"plugins: chat requested unknown skill '{plugin}:{skill}'")
        else:
            parts.append(f"# Skill: {skill_spec.name}\n\n{skill_spec.body_md}")
    return "\n\n".join(parts) if parts else None


def builtin_tool_defs(active_plugin: Optional[PluginSpec]) -> list[dict]:
    """Return the OpenAI-shape tool defs from the in-process mcp_servers/*
    modules. Filtered by the active plugin's declared_mcp_servers — a plugin
    that says nothing about MCPs gets no built-in tools, which is the safe
    default (the model won't see unrelated tools it might mis-pick)."""
    if active_plugin is None or not active_plugin.declared_mcp_servers:
        return []
    declared = set(active_plugin.declared_mcp_servers)
    out: list[dict] = []
    # Late-imports keep this module side-effect-free at import time. Missing
    # mcp_servers modules don't error — week 1 PR ships plugins.py before
    # mcp_servers/, the chat handler simply sees zero tools until then.
    for server_name, module_path in [
        ("mcp-locallyai-search", "mcp_servers.search.server"),
        ("mcp-locallyai-audit", "mcp_servers.audit.server"),
        ("mcp-locallyai-matter", "mcp_servers.matter.server"),
        ("mcp-locallyai-citation", "mcp_servers.citation.server"),
    ]:
        if server_name not in declared:
            continue
        # Marketplace toggle: the admin can disable individual MCP servers
        # without uninstalling them. Disabled = invisible to the model
        # (same effect as the plugin not declaring it).
        if not is_mcp_enabled(server_name):
            continue
        try:
            mod = __import__(module_path, fromlist=["TOOL_DEFS"])
        except ImportError:
            log.warning(f"plugins: declared MCP '{server_name}' not installed, skipping")
            continue
        defs = getattr(mod, "TOOL_DEFS", None)
        if isinstance(defs, list):
            out.extend(defs)
    return out


def dispatch_builtin_tool(
    name: str,
    arguments: dict,
    user: str,
    matter_code: Optional[str] = None,
) -> dict:
    """Look up the tool function in the in-process mcp_servers/* dispatch
    tables and call it. Returns the JSON-serialisable result the chat
    handler hands back to the model as a role="tool" message.

    Tool name collision across servers is resolved first-match-wins in the
    order: search, audit, matter, citation. Plugins should not declare
    overlapping tool names; the loader rejects duplicate-name plugins later.
    """
    for server_name, module_path in (
        ("mcp-locallyai-search", "mcp_servers.search.server"),
        ("mcp-locallyai-audit", "mcp_servers.audit.server"),
        ("mcp-locallyai-matter", "mcp_servers.matter.server"),
        ("mcp-locallyai-citation", "mcp_servers.citation.server"),
    ):
        try:
            mod = __import__(module_path, fromlist=["DISPATCH"])
        except ImportError:
            continue
        dispatch = getattr(mod, "DISPATCH", None)
        if isinstance(dispatch, dict) and name in dispatch:
            # Marketplace gate — refuse dispatch on disabled MCP servers
            # even if the model somehow guessed the tool name. The toggle
            # is the source of truth, not just whether the tool def was
            # surfaced.
            if not is_mcp_enabled(server_name):
                return {"error": f"tool '{name}' belongs to disabled MCP server '{server_name}'"}
            try:
                return dispatch[name](arguments, user=user, matter_code=matter_code)
            except Exception as exc:
                log.error(f"plugins: tool '{name}' raised: {exc}", exc_info=True)
                return {"error": str(exc), "tool": name}
    return {"error": f"unknown tool: {name}"}


# ── FastAPI router ────────────────────────────────────────────────────────────

router = APIRouter()
_limiter = Limiter(key_func=get_remote_address)


@router.get("/v1/plugins")
def list_plugins_endpoint(key=Depends(_auth)) -> list[dict]:
    """List installed plugins and their skill metadata. Skill bodies are NOT
    returned here — fetch one at a time via /v1/plugins/{name}/skill."""
    return list_plugins()


@router.get("/v1/plugins/{name}/skill")
def get_skill_endpoint(
    name: str,
    skill: str = Query(..., max_length=64, pattern=r"^[a-z0-9\-]{1,64}$"),
    key=Depends(_auth),
) -> dict:
    """Return the SKILL.md body for one skill of one plugin. Used by
    worker-ui for the plugin detail panel, and by the operator for
    debugging 'what is the model actually seeing?'"""
    if not _NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="invalid plugin name")
    spec = get_skill(name, skill)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown skill: {name}/{skill}")
    return {
        "plugin": name,
        "skill": spec.name,
        "description": spec.description,
        "body_md": spec.body_md,
    }


# ── Marketplace endpoints (admin-only) ────────────────────────────────────────


@router.get("/admin/marketplace")
def admin_marketplace(key=Depends(_admin_auth)) -> dict:
    """Manager UI's plugin + MCP-server marketplace view. Returns the FULL
    installed catalogue, including disabled entries, with their current
    enabled state. /v1/plugins by contrast hides disabled plugins
    (matches what chat consumers see)."""
    return {
        "plugins": list_plugins(include_disabled=True),
        "mcp_servers": [
            {
                "name": srv,
                "enabled": is_mcp_enabled(srv),
                # tool count is best-effort — if the module isn't importable
                # (e.g. an older deployment), count 0.
                "tool_count": _safe_tool_count(srv),
            }
            for srv in _KNOWN_MCP_SERVERS
        ],
        "state_file": str(_MARKETPLACE_STATE_FILE),
    }


def _safe_tool_count(server_name: str) -> int:
    module_map = {
        "mcp-locallyai-search":   "mcp_servers.search.server",
        "mcp-locallyai-audit":    "mcp_servers.audit.server",
        "mcp-locallyai-matter":   "mcp_servers.matter.server",
        "mcp-locallyai-citation": "mcp_servers.citation.server",
    }
    module_path = module_map.get(server_name)
    if not module_path:
        return 0
    try:
        mod = __import__(module_path, fromlist=["TOOL_DEFS"])
    except ImportError:
        return 0
    defs = getattr(mod, "TOOL_DEFS", None)
    return len(defs) if isinstance(defs, list) else 0


@router.post("/admin/plugins/{name}/enable")
def admin_enable_plugin(name: str, key=Depends(_admin_auth)) -> dict:
    if not _NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="invalid plugin name")
    if name not in _PLUGIN_REGISTRY:
        raise HTTPException(status_code=404, detail=f"plugin not installed: {name}")
    set_plugin_enabled(name, True)
    log.info(f"plugins: marketplace ENABLE plugin={name}")
    return {"plugin": name, "enabled": True}


@router.post("/admin/plugins/{name}/disable")
def admin_disable_plugin(name: str, key=Depends(_admin_auth)) -> dict:
    if not _NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="invalid plugin name")
    if name not in _PLUGIN_REGISTRY:
        raise HTTPException(status_code=404, detail=f"plugin not installed: {name}")
    set_plugin_enabled(name, False)
    log.info(f"plugins: marketplace DISABLE plugin={name}")
    return {"plugin": name, "enabled": False}


@router.post("/admin/mcp-servers/{name}/enable")
def admin_enable_mcp(name: str, key=Depends(_admin_auth)) -> dict:
    if name not in _KNOWN_MCP_SERVERS:
        raise HTTPException(status_code=404, detail=f"unknown MCP server: {name}")
    set_mcp_enabled(name, True)
    log.info(f"plugins: marketplace ENABLE mcp_server={name}")
    return {"mcp_server": name, "enabled": True}


@router.post("/admin/mcp-servers/{name}/disable")
def admin_disable_mcp(name: str, key=Depends(_admin_auth)) -> dict:
    if name not in _KNOWN_MCP_SERVERS:
        raise HTTPException(status_code=404, detail=f"unknown MCP server: {name}")
    set_mcp_enabled(name, False)
    log.info(f"plugins: marketplace DISABLE mcp_server={name}")
    return {"mcp_server": name, "enabled": False}
