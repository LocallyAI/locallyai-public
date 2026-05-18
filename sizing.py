"""sizing.py — per-firm hardware + model recommendation engine.

**The primary surface is now the vendor monitor dashboard's Sizing
tab** (`docs/monitor/cloudflare-worker/src/dashboard/index.html`).
That dashboard runs the same logic in-browser (TypeScript-equivalent),
so the vendor sales/onboarding team uses one tool from one place
without needing a deployment to call.

This Python module is kept as the **CLI / scripting** version of the
same engine — `scripts/onboard_firm.sh` and ad-hoc Python sessions
can import it. If the catalog or scoring differs from the dashboard,
the dashboard wins (it's the surface the sales team actually uses).
When updating: edit BOTH this file AND
`docs/monitor/cloudflare-worker/src/dashboard/index.html`'s embedded
catalog. The dashboard is canonical at render time; this module is
canonical for back-end / scripting consumers.

Vendor policy: Q4 quants are excluded from the catalog. Q4 saves RAM
but degrades quality enough that some legal-output tasks become
unreliable. We standardise on Q8 (8-bit) for the small/medium RAM
budgets and FP16/BF16 (full half-precision) for the high-budget tier.
This is a deliberate quality-floor decision — operators who want Q4
can override LOCALLYAI_MODEL via .env, but the catalog never
recommends it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


# ── Hardware catalog (Mac Studio configs commonly available) ────────────────
# Indicative GBP prices from late 2025 retail; refresh quarterly.
# `headroom_gb` is RAM left for the OS + embedder + Qdrant + concurrent
# request KV caches AFTER subtracting the model weights.
HARDWARE_CATALOG = [
    {"sku": "Mac Studio M4 Max 36GB",   "ram_gb":  36, "price_gbp_approx": 2099, "chip": "M4 Max"},
    {"sku": "Mac Studio M4 Max 48GB",   "ram_gb":  48, "price_gbp_approx": 2299, "chip": "M4 Max"},
    {"sku": "Mac Studio M4 Max 64GB",   "ram_gb":  64, "price_gbp_approx": 2599, "chip": "M4 Max"},
    {"sku": "Mac Studio M4 Max 96GB",   "ram_gb":  96, "price_gbp_approx": 2999, "chip": "M4 Max"},
    {"sku": "Mac Studio M4 Max 128GB",  "ram_gb": 128, "price_gbp_approx": 3499, "chip": "M4 Max"},
    {"sku": "Mac Studio M3 Ultra 96GB", "ram_gb":  96, "price_gbp_approx": 4199, "chip": "M3 Ultra"},
    {"sku": "Mac Studio M3 Ultra 128GB","ram_gb": 128, "price_gbp_approx": 4499, "chip": "M3 Ultra"},
    {"sku": "Mac Studio M3 Ultra 192GB","ram_gb": 192, "price_gbp_approx": 5499, "chip": "M3 Ultra"},
    {"sku": "Mac Studio M3 Ultra 256GB","ram_gb": 256, "price_gbp_approx": 6999, "chip": "M3 Ultra"},
    {"sku": "Mac Studio M3 Ultra 512GB","ram_gb": 512, "price_gbp_approx": 9499, "chip": "M3 Ultra"},
]


# ── Model catalog — Q8 (8-bit) and FP16/BF16 (half-precision) only ──────────
# Q4 quants intentionally excluded — see module docstring.
#
# size_gb is the on-disk and roughly-in-RAM size of the weights.
# multilingual_quality is rated for Arabic / KSA legal use:
#   "best" = strong native multilingual training (Qwen2.5+, Command-R+)
#   "good" = decent multilingual with some quality gap (Llama 3.1+)
#   "fair" = English-strong, will work but quality drops in Arabic
#
# tokens_per_second is a rough estimate on M3 Ultra for short prompts;
# scales roughly inversely with model size and with KV-cache pressure.

MODEL_CATALOG = [
    # ── Small (7-8B) ────────────────────────────────────────────────────────
    {"id": "mlx-community/Mistral-7B-Instruct-v0.3-8bit",
     "family": "Mistral", "params_b": 7, "quant": "Q8", "size_gb": 7,
     "multilingual_quality": "fair", "tier": "small", "tps_m3ultra": 90},
    {"id": "mlx-community/Qwen2.5-7B-Instruct-8bit",
     "family": "Qwen2.5", "params_b": 7, "quant": "Q8", "size_gb": 8,
     "multilingual_quality": "best", "tier": "small", "tps_m3ultra": 85},
    {"id": "mlx-community/Llama-3.1-8B-Instruct-8bit",
     "family": "Llama", "params_b": 8, "quant": "Q8", "size_gb": 8,
     "multilingual_quality": "good", "tier": "small", "tps_m3ultra": 80},

    # ── Medium (13-32B) ─────────────────────────────────────────────────────
    {"id": "mlx-community/Qwen2.5-14B-Instruct-8bit",
     "family": "Qwen2.5", "params_b": 14, "quant": "Q8", "size_gb": 15,
     "multilingual_quality": "best", "tier": "medium", "tps_m3ultra": 55},
    {"id": "mlx-community/Mixtral-8x7B-Instruct-v0.1-8bit",
     "family": "Mixtral", "params_b": 47, "params_b_active": 13, "quant": "Q8", "size_gb": 47,
     "multilingual_quality": "good", "tier": "medium", "tps_m3ultra": 50,
     "note": "MoE — 47B total, 13B active per token"},
    {"id": "mlx-community/Qwen2.5-32B-Instruct-8bit",
     "family": "Qwen2.5", "params_b": 32, "quant": "Q8", "size_gb": 32,
     "multilingual_quality": "best", "tier": "medium", "tps_m3ultra": 35},

    # ── Large (70B+) ────────────────────────────────────────────────────────
    {"id": "mlx-community/Llama-3.3-70B-Instruct-8bit",
     "family": "Llama", "params_b": 70, "quant": "Q8", "size_gb": 70,
     "multilingual_quality": "good", "tier": "large", "tps_m3ultra": 18},
    {"id": "mlx-community/Qwen2.5-72B-Instruct-8bit",
     "family": "Qwen2.5", "params_b": 72, "quant": "Q8", "size_gb": 72,
     "multilingual_quality": "best", "tier": "large", "tps_m3ultra": 17},

    # ── Very large / FP16 (highest fidelity) ────────────────────────────────
    {"id": "mlx-community/Qwen2.5-72B-Instruct-bf16",
     "family": "Qwen2.5", "params_b": 72, "quant": "BF16", "size_gb": 145,
     "multilingual_quality": "best", "tier": "very_large", "tps_m3ultra": 9},
    {"id": "mlx-community/Llama-3.3-70B-Instruct-bf16",
     "family": "Llama", "params_b": 70, "quant": "BF16", "size_gb": 140,
     "multilingual_quality": "good", "tier": "very_large", "tps_m3ultra": 9},
    {"id": "mlx-community/Mixtral-8x22B-Instruct-v0.1-8bit",
     "family": "Mixtral", "params_b": 141, "params_b_active": 39, "quant": "Q8", "size_gb": 141,
     "multilingual_quality": "good", "tier": "very_large", "tps_m3ultra": 11,
     "note": "MoE — 141B total, 39B active per token"},
]


# ── Embedder choices ────────────────────────────────────────────────────────
EMBEDDERS = {
    "UK": {
        "id": "nomic-embed-text:latest",
        "rationale": "English-strong, fast, ~140M params; runs on any RAM tier.",
        "ram_gb": 1,
    },
    "KSA": {
        "id": "intfloat/multilingual-e5-base",
        "rationale": "Multilingual (Arabic + English at parity), 278M params; required for KSA per LocallyAI policy.",
        "ram_gb": 2,
    },
}


# ── Input + output dataclasses ──────────────────────────────────────────────
@dataclass
class SizingProfile:
    users_total: int = 10
    users_concurrent_peak: int = 2
    corpus_gb_estimate: float = 10.0
    region: Literal["UK", "KSA"] = "UK"
    use_case: Literal["chat", "research", "mixed"] = "mixed"
    ha_required: bool = False
    latency_target_ms: int = 5000
    quality_preference: Literal["balanced", "fidelity", "throughput"] = "balanced"


# ── Heuristics ──────────────────────────────────────────────────────────────
def _ram_required(model: dict, concurrent_users: int) -> int:
    """Estimate total RAM needed in GB for a given model + concurrency.

    Components:
      - Model weights (in unified memory)
      - KV cache per concurrent inference (~5% of model size per user
        at typical context lengths; varies with prompt length)
      - System overhead (~8 GB for macOS + background processes)
      - Embedder weights (1-2 GB depending on region)
      - Qdrant + retrieval pipeline (~4 GB working set)
      - Safety headroom for ingest spikes (~4 GB)

    Result is rounded UP to a 4 GB boundary to make matching the Mac
    Studio SKU catalog cleaner.
    """
    model_gb = model["size_gb"]
    kv_per_user_gb = max(1.0, model_gb * 0.05)
    overhead_gb = 8 + 2 + 4 + 4  # system + embedder + qdrant + headroom
    total = model_gb + (kv_per_user_gb * concurrent_users) + overhead_gb
    # Round up to nearest 4 GB.
    return int((total + 3.99) // 4 * 4)


def _select_hardware(ram_needed_gb: int) -> dict:
    """Pick the cheapest Mac Studio SKU that fits ram_needed_gb,
    breaking ties in favour of M3 Ultra (more memory bandwidth)."""
    candidates = [h for h in HARDWARE_CATALOG if h["ram_gb"] >= ram_needed_gb]
    if not candidates:
        # Beyond catalog — return the biggest available with a flag.
        biggest = max(HARDWARE_CATALOG, key=lambda h: h["ram_gb"])
        return {**biggest, "exceeds_catalog": True,
                "warning": f"Profile needs ~{ram_needed_gb} GB; catalog tops out at {biggest['ram_gb']} GB. "
                           "Consider HA (split load across two nodes) or a smaller model tier."}
    # Prefer M3 Ultra at same RAM (Ultra has 2x the bandwidth of Max).
    candidates.sort(key=lambda h: (h["ram_gb"], 0 if "Ultra" in h["chip"] else 1, h["price_gbp_approx"]))
    return candidates[0]


def _select_model(profile: SizingProfile) -> tuple[dict, list[dict]]:
    """Pick the primary model + 2 alternatives based on the profile.

    Rules:
      - KSA → multilingual_quality must be 'best' or 'good'
      - quality_preference=fidelity → prefer BF16 over Q8
      - quality_preference=throughput → prefer smaller params (faster tps)
      - users_concurrent_peak high (≥10) → prefer smaller (lower KV pressure)
      - Otherwise default to balanced: 14B Q8 (Qwen2.5) for UK,
        14B Q8 (Qwen2.5) for KSA — same model family covers both.
    """
    eligible = [m for m in MODEL_CATALOG if m["quant"] in ("Q8", "BF16")]
    if profile.region == "KSA":
        eligible = [m for m in eligible if m["multilingual_quality"] in ("best", "good")]

    # Score each model. Lower score = better fit.
    # Targets:
    #  - small firm (<15 users) balanced → 14B Q8 (medium tier, single Mac)
    #  - mid firm (15-40 users) balanced → 14-32B Q8
    #  - large firm (>40 users) balanced → 32-70B Q8
    #  - fidelity preference → biggest within RAM budget (BF16 if possible)
    #  - throughput preference → smaller for tps; penalty for big
    def score(m: dict) -> float:
        s = 0.0
        # Concurrency pressure → prefer smaller
        if profile.users_concurrent_peak >= 10:
            s += m["params_b"] * 0.5
        elif profile.users_concurrent_peak >= 5:
            s += m["params_b"] * 0.2
        # Firm-size guidance — penalise over-sized recommendations
        # (small firms don't need 70B; quality is wasted at low corpus)
        if profile.quality_preference == "balanced":
            if profile.users_total < 15 and m["params_b"] > 14:
                s += (m["params_b"] - 14) * 0.8  # strong penalty
            if profile.users_total < 40 and m["params_b"] > 32:
                s += (m["params_b"] - 32) * 0.4
        # Tier preference for balanced — medium is the sweet spot
        if profile.quality_preference == "balanced":
            if m["tier"] == "medium": s -= 8
            elif m["tier"] == "very_large": s += 25
            elif m["tier"] == "small": s += 3 if profile.users_total >= 15 else -2
        # Fidelity → prefer BF16 + larger
        if profile.quality_preference == "fidelity":
            if m["quant"] == "BF16": s -= 30
            s -= m["params_b"] * 0.5
        # Throughput → smaller wins
        if profile.quality_preference == "throughput":
            s += m["params_b"] * 1.0
            if m["tier"] == "small": s -= 5
        # KSA strongly prefers 'best' multilingual
        if profile.region == "KSA" and m["multilingual_quality"] == "good":
            s += 5
        # Mistral-7B is too small for production legal use; gentle nudge away
        # except in throughput-pref scenarios
        if m["params_b"] < 8 and profile.quality_preference != "throughput":
            s += 5
        # MoE models (Mixtral) — heavier RAM footprint for the same active-
        # params benefit; penalise unless the firm is large enough to use it
        if "params_b_active" in m and profile.users_total < 30:
            s += 8
        return s

    eligible.sort(key=score)
    primary = eligible[0]
    alternatives = eligible[1:3]
    return primary, alternatives


def _expected_performance(model: dict, hardware: dict, profile: SizingProfile) -> dict:
    """Rough performance envelope. tps_m3ultra is the catalog baseline;
    M4 Max gets ~60% of that (lower memory bandwidth)."""
    base_tps = model.get("tps_m3ultra", 30)
    if "M4 Max" in hardware["chip"]:
        eff_tps = int(base_tps * 0.6)
    else:
        eff_tps = base_tps
    # Concurrency cost — scales tps down with active inferences
    concurrent_tps = max(1, int(eff_tps / max(1, profile.users_concurrent_peak * 0.6)))
    cold_load_s = max(5, int(model["size_gb"] * 0.4))
    return {
        "tokens_per_second_single_user": eff_tps,
        "tokens_per_second_per_user_at_peak": concurrent_tps,
        "cold_load_seconds": cold_load_s,
        "concurrent_users_supported": profile.users_concurrent_peak,
        "latency_p50_short_prompt_ms": int(1000 / max(1, eff_tps) * 50),
        "latency_p95_long_prompt_ms": int(profile.latency_target_ms * 0.8),
    }


def _checklist(profile: SizingProfile, recommendation: dict) -> list[dict]:
    """Build the dynamic onboarding checklist. Includes only the items
    relevant to this profile (HA steps appear if HA; KSA steps appear
    if KSA region; bulk-ingest appears if corpus > 5 GB)."""
    items: list[dict] = []

    def add(category: str, required: bool, label: str, ref: str | None = None):
        items.append({"category": category, "required": required, "label": label, "sop_ref": ref})

    # Pre-install ─────────────────────────────────────────────────────────
    add("pre-install", True, "Confirm hardware ordered: " + recommendation["hardware"]["sku"],
        "vendor-sop/vendor-sales.md")
    add("pre-install", True, "Order form + DPA signed and filed in vendor-records",
        "vendor-sop/vendor-onboarding.md")
    add("pre-install", True, "Confirm office network: static IP for the Mac, allow outbound HTTPS to github.com / huggingface.co / *.workers.dev",
        "sop/data-isolation.md")
    if profile.ha_required:
        add("pre-install", True, "Order SECOND identical Mac Studio for HA failover",
            "sop/setup-mac-ha.md")
    if profile.region == "KSA":
        add("pre-install", True, "Confirm KSA data residency in DPA (PDPL Art. 29) + Arabic UI requirement",
            "sop/setup-saudi.md")
        add("pre-install", True, "Translate firm-specific demo docs into Arabic for first-run",
            "sop/setup-saudi.md")

    # Install ─────────────────────────────────────────────────────────────
    add("install", True, "Enable FileVault on the office Mac BEFORE running install.sh",
        "sop/setup-mac-single.md")
    add("install", True, f"Run install.sh with LOCALLYAI_DATA_REGION={profile.region}",
        "sop/setup-mac-single.md")
    add("install", True, f"Set LOCALLYAI_MODEL to {recommendation['model']['id']} in .env",
        "sop/maintenance.md")
    add("install", True, f"Set EMBED_MODEL to {recommendation['embedder']['id']} in .env",
        "sop/maintenance.md")
    if profile.ha_required:
        add("install", True, "Pair second Mac as HA peer (Syncthing + shared SHARED_DIR)",
            "sop/setup-mac-ha.md")
    add("install", True, "Pre-pull the LLM (warms HF cache before going live)",
        "runbooks/add-new-firm.md")
    add("install", True, "Capture the printed admin key into vendor-records (encrypted with firm PGP)",
        "runbooks/add-new-firm.md")

    # Data ingest ─────────────────────────────────────────────────────────
    if profile.corpus_gb_estimate >= 5:
        add("ingest", True, f"Plan bulk ingest of ~{profile.corpus_gb_estimate:.0f} GB corpus (off-hours; expect ~{int(profile.corpus_gb_estimate * 8)} min)",
            "sop/bulk-ingest.md")
    if profile.corpus_gb_estimate >= 50:
        add("ingest", False, "Consider chunk-size + RELEVANCE_FLOOR tuning for large corpus (see incidents-service.md §B)",
            "sop/incidents-service.md")

    # Distribution to staff laptops ────────────────────────────────────────
    add("distribution", True, "Build per-firm staff apps: bash scripts/build_staff_apps.sh",
        "scripts/build_staff_apps.sh")
    add("distribution", True, "Distribute LocallyAI Trust Cert.zip to all staff laptops (run BEFORE the apps)",
        "scripts/build_staff_apps.sh")
    add("distribution", True, f"Distribute LocallyAI Manager.app.zip to {max(1, profile.users_total // 10)} DPO/admin users",
        "apps/manager-desktop/README.md")
    add("distribution", True, f"Distribute LocallyAI Workspace.app.zip OR Windows zip to {profile.users_total} lawyers",
        "apps/worker-desktop/README.md")

    # First-week ──────────────────────────────────────────────────────────
    add("first-week", True, "Generate baseline compliance snapshot (day-zero filing)",
        "runbooks/dpo-monthly-snapshot.md")
    add("first-week", True, "Acknowledge the firm on the vendor monitor dashboard",
        "runbooks/add-new-firm.md")
    add("first-week", True, "Confirm first heartbeat reached Cloudflare (verify firm_id in monitor)",
        "runbooks/add-new-firm.md")
    add("first-week", False, "Record first round of user training events (AI-output review, GDPR fundamentals)",
        "sop/dpo-compliance-portal.md")
    if profile.region == "KSA":
        add("first-week", True, "Run setup-saudi.md verification checklist (Arabic UI, RTL, Hijri dates)",
            "sop/setup-saudi.md")

    # Ongoing ─────────────────────────────────────────────────────────────
    add("ongoing", True, "Schedule monthly compliance snapshot in vendor CS calendar",
        "vendor-sop/vendor-customer-success.md")
    add("ongoing", True, "Schedule quarterly backup-restore test + record attestation",
        "sop/dpo-compliance-portal.md")
    add("ongoing", False, "Schedule annual salt rotation in vendor maintenance window",
        "sop/maintenance.md")

    return items


def recommend(profile: SizingProfile) -> dict:
    """Top-level recommendation function. Input: profile. Output: a
    dict suitable for direct JSON serialisation by the API endpoint."""
    primary_model, alternatives = _select_model(profile)
    ram_needed = _ram_required(primary_model, profile.users_concurrent_peak)
    hardware = _select_hardware(ram_needed)
    embedder = EMBEDDERS[profile.region]

    warnings: list[str] = []
    if profile.users_concurrent_peak > profile.users_total:
        warnings.append("Concurrent peak exceeds total users — sanity-check input.")
    if profile.users_concurrent_peak >= 10 and not profile.ha_required:
        warnings.append(f"Concurrency ≥ {profile.users_concurrent_peak} typically warrants HA (failover during peak hours).")
    if hardware.get("exceeds_catalog"):
        warnings.append(hardware["warning"])
    if profile.region == "KSA" and "Qwen" not in primary_model["family"]:
        warnings.append("Non-Qwen model for KSA — Arabic quality may be lower; consider Qwen2.5 family.")
    if primary_model["size_gb"] >= 100 and not profile.ha_required:
        warnings.append("Model size ≥ 100 GB — single-node deployment leaves no failover margin; consider HA.")

    recommendation = {
        "profile": asdict(profile),
        "hardware": {
            **{k: v for k, v in hardware.items() if k not in ("exceeds_catalog", "warning")},
            "ram_needed_estimate_gb": ram_needed,
            "storage_gb_recommended": max(1000, int(profile.corpus_gb_estimate * 10) + 500),
            "rationale": (
                f"Model weights ~{primary_model['size_gb']} GB + "
                f"~{int(primary_model['size_gb'] * 0.05 * profile.users_concurrent_peak)} GB KV cache at peak "
                f"+ ~18 GB system/embedder/Qdrant/headroom = ~{ram_needed} GB needed. "
                f"Cheapest catalog SKU that fits: {hardware['sku']}."
            ),
        },
        "model": {
            **primary_model,
            "rationale": _explain_model_choice(primary_model, profile),
        },
        "alternatives": [
            {**m, "rationale": _explain_model_choice(m, profile)}
            for m in alternatives
        ],
        "embedder": embedder,
        "expected_performance": _expected_performance(primary_model, hardware, profile),
        "ha_recommended": profile.ha_required or primary_model["size_gb"] >= 100 or profile.users_concurrent_peak >= 10,
        "warnings": warnings,
    }
    recommendation["checklist"] = _checklist(profile, recommendation)
    return recommendation


def _explain_model_choice(m: dict, profile: SizingProfile) -> str:
    parts = []
    if profile.region == "KSA":
        if m["multilingual_quality"] == "best":
            parts.append("multilingual_quality=best (strong Arabic).")
        else:
            parts.append("multilingual_quality=good (acceptable Arabic; not native).")
    if m["quant"] == "BF16":
        parts.append("BF16 (half-precision, no quantisation loss).")
    else:
        parts.append("Q8 (8-bit quant, ~99% of BF16 quality at half the RAM).")
    parts.append(f"{m['params_b']}B parameters, ~{m['size_gb']} GB on disk.")
    if profile.use_case == "research":
        parts.append("Suits research / long-context drafting.")
    if profile.quality_preference == "throughput":
        parts.append("Smaller param count to maintain throughput at peak concurrency.")
    return " ".join(parts)
