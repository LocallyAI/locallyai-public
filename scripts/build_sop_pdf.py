#!/usr/bin/env python3
"""
build_sop_pdf.py

Render the SOP markdown set into a print-friendly single PDF.

Pipeline:
  1. Read docs/SOP.md + every chapter under docs/sop/ in TOC order.
  2. Concatenate into one big HTML document with a print stylesheet
     (numbered sections, page breaks per chapter, footer with page
     number, bookmarks/outline via <h1> anchors).
  3. Invoke Chrome / Chromium in headless mode with --print-to-pdf.

Why Chrome and not pandoc/wkhtmltopdf:
  - Pandoc needs LaTeX (4 GB install) for good output. Chrome is
    already on every Mac/Win box that has the deployment.
  - wkhtmltopdf is unmaintained.
  - Chrome's print engine handles modern CSS, ligatures, and code
    blocks correctly with zero install.

If pandoc is present we use it as a faster path that produces a
slightly nicer-looking PDF. Without it, Chrome is the fallback.

Run:
  .venv/bin/python scripts/build_sop_pdf.py
  # → dist/locallyai-sop-vYYYYMMDD-HHMM.pdf
"""
from __future__ import annotations
import argparse
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

# Two SOPs: firm-facing (default) and vendor-internal (--vendor flag).
# Each has its own master + chapter dir + chapter order + output name.

FIRM_SOP_MASTER = DOCS / "SOP.md"
FIRM_SOP_DIR = DOCS / "sop"
FIRM_CHAPTER_ORDER = [
    "repo-access.md",
    "setup-mac-single.md",
    "setup-mac-ha.md",
    "setup-windows.md",
    "setup-saudi.md",
    "daily.md",
    "bulk-ingest.md",
    "one-click-start.md",
    "client-install.md",
    "updates.md",
    "data-isolation.md",
    "vendor-monitoring.md",
    "onboarding.md",
    "remote-access.md",
    "install-checklist.md",
    "maintenance.md",
    "compliance.md",
    "compliance-saudi.md",
    "dpo-compliance-portal.md",
    "sizing.md",
    "document-acl.md",
    "ha-architecture.md",
    "dms-integration.md",
    "incidents-software.md",
    "incidents-physical.md",
    "incidents-security.md",
    "incidents-operator.md",
    "incidents-people.md",
    "incidents-legal.md",
    "incidents-misuse.md",
    "incidents-service.md",
    "incidents-supply.md",
    "scale-out.md",
    "recovery.md",
    "decommission.md",
    "conflict-checks.md",
    "document-comparison.md",
    "citation-checker.md",
    "roadmap.md",
    "CHANGELOG.md",
]

VENDOR_SOP_MASTER = DOCS / "VENDOR_SOP.md"
VENDOR_SOP_DIR = DOCS / "vendor-sop"
# Operator runbooks live outside the vendor-sop/ directory but appear at
# the FRONT of the vendor PDF — they're what a non-founder operator
# opens first during an incident or scheduled operation. Resolved
# specially in read_chapters() because they're not in SOP_DIR.
RUNBOOKS_DIR = DOCS / "runbooks"
RUNBOOKS_ORDER = [
    "00-index.md",
    "dpo-monthly-snapshot.md",
    "api-down.md",
    "add-new-firm.md",
    "remove-firm.md",
    "audit-chain-broken.md",
    "dashboard-locked-out.md",
    "failover-test.md",
    "rotate-deploy-keys-pat.md",
    "conflict-check.md",
]

# Supplemental references that SOP chapters frequently link to. Bundled
# in both firm and vendor PDFs as appendices so cross-references like
# `[../iso27001-controls.md](../iso27001-controls.md)` resolve to an
# in-PDF anchor jump. Each entry is (REPO-relative path, pretty title).
APPENDIX_FILES = [
    (DOCS / "iso27001-controls.md", "Appendix A — ISO 27001:2022 controls map"),
    (DOCS / "qdrant-ha.md", "Appendix B — Qdrant HA setup"),
    (DOCS / "syncthing-setup.md", "Appendix C — Syncthing setup"),
    (REPO / "DPA_DRAFT.md", "Appendix D — DPA template (UK)"),
    (REPO / "DPA_DRAFT_SA.md", "Appendix E — DPA template (KSA / PDPL)"),
]

VENDOR_CHAPTER_ORDER = [
    "vendor-team.md",
    "vendor-infrastructure.md",
    "vendor-daily-ops.md",
    "vendor-release-engineering.md",
    "vendor-incidents-own-infra.md",
    "vendor-disaster-recovery.md",
    "vendor-sales.md",
    "vendor-onboarding.md",
    "vendor-customer-success.md",
    "vendor-sub-processors.md",
    "vendor-compliance.md",
    "vendor-people.md",
    "vendor-internal-dryrun.md",
    "CHANGELOG.md",
]

# Default: firm-side SOP. The --vendor flag flips this in main().
SOP_MASTER = FIRM_SOP_MASTER
SOP_DIR = FIRM_SOP_DIR
CHAPTER_ORDER = FIRM_CHAPTER_ORDER
SOP_TITLE = "LocallyAI SOP"
SOP_SUBTITLE = "Standard Operating Procedure"
OUTPUT_PREFIX = "locallyai-sop"

PRINT_CSS = r"""
@page {
    size: A4;
    margin: 18mm 16mm 22mm 16mm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: -apple-system, system-ui, sans-serif;
        font-size: 9pt;
        color: #888;
    }
    @top-center {
        content: "__SOP_TITLE__";
        font-family: -apple-system, system-ui, sans-serif;
        font-size: 9pt;
        color: #888;
    }
}
html { font-size: 11pt; }
body {
    font-family: "Charter", "Georgia", serif;
    color: #111;
    line-height: 1.45;
    max-width: none;
    margin: 0;
    padding: 0;
    counter-reset: chapter;
}
.cover {
    text-align: center;
    padding: 30vh 0 0;
    page-break-after: always;
}
.cover h1 {
    font-size: 48pt;
    margin: 0 0 0.2em;
    border: none;
}
.cover .subtitle {
    font-size: 14pt;
    color: #666;
    margin-bottom: 2em;
}
.cover .meta {
    font-size: 10pt;
    color: #888;
    margin-top: 30vh;
}
.toc {
    page-break-after: always;
}
.toc h1 { font-size: 22pt; }
.toc ol { padding-left: 1.2em; }
.toc li { margin: 0.3em 0; font-size: 11pt; }
.toc a { color: #111; text-decoration: none; }
.chapter {
    page-break-before: always;
    counter-increment: chapter;
}
h1 {
    font-family: -apple-system, "Helvetica Neue", system-ui, sans-serif;
    font-size: 22pt;
    border-bottom: 2px solid #111;
    padding-bottom: 0.2em;
    margin-top: 0;
}
h2 {
    font-family: -apple-system, "Helvetica Neue", system-ui, sans-serif;
    font-size: 15pt;
    margin-top: 1.5em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.15em;
    page-break-after: avoid;
}
h3 {
    font-family: -apple-system, "Helvetica Neue", system-ui, sans-serif;
    font-size: 12pt;
    margin-top: 1.2em;
    page-break-after: avoid;
}
h4 { font-size: 11pt; margin-top: 1em; page-break-after: avoid; }
p { orphans: 2; widows: 2; }
ul, ol { margin: 0.4em 0 0.7em 1.4em; padding: 0; }
li { margin: 0.2em 0; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.6em 0;
    page-break-inside: avoid;
    font-size: 10pt;
}
th, td {
    border: 1px solid #ccc;
    padding: 4px 8px;
    text-align: left;
    vertical-align: top;
}
th { background: #f3f3f3; font-weight: 600; }
code {
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 9.5pt;
    background: #f5f5f5;
    padding: 0 3px;
    border-radius: 2px;
}
pre {
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 9pt;
    background: #f5f5f5;
    padding: 8px 10px;
    border-radius: 3px;
    border-left: 3px solid #888;
    overflow-wrap: break-word;
    white-space: pre-wrap;
    word-wrap: break-word;
    page-break-inside: avoid;
    line-height: 1.35;
}
pre code { background: none; padding: 0; font-size: 9pt; }
blockquote {
    border-left: 3px solid #888;
    padding: 0.4em 0.8em;
    margin: 0.6em 0;
    background: #fafafa;
    font-style: italic;
    color: #444;
    page-break-inside: avoid;
}
a { color: #1a5fb4; text-decoration: none; }
a:hover { text-decoration: underline; }
hr {
    border: none;
    border-top: 1px solid #ddd;
    margin: 1.5em 0;
}

/* avoid widow / orphan ugliness on common patterns */
table, pre, blockquote, ul, ol { page-break-inside: avoid; }
h1, h2, h3 { page-break-inside: avoid; }
"""


def read_chapters() -> list[tuple[str, str]]:
    """Return [(chapter_path_relative, markdown_text), …] in TOC order.

    Firm PDF: master index + firm chapters only.
    Vendor PDF: master index + runbooks + firm chapters + vendor chapters.
    The vendor engineer needs every firm-side procedure (they install,
    operate, maintain) AS WELL AS vendor-only material — bundling them
    into one document lets cross-references between firm and vendor
    chapters all resolve to in-PDF anchors."""
    out = []
    if not SOP_MASTER.exists():
        sys.exit(f"{SOP_MASTER} missing — nothing to render")
    master_rel = SOP_MASTER.relative_to(REPO).as_posix()
    out.append((master_rel, SOP_MASTER.read_text(encoding="utf-8")))

    is_vendor = SOP_DIR == VENDOR_SOP_DIR

    # In vendor mode, also bundle the firm SOP master so vendor and firm
    # chapters that link to `SOP.md` resolve to an in-PDF anchor.
    if is_vendor:
        firm_master_rel = FIRM_SOP_MASTER.relative_to(REPO).as_posix()
        if FIRM_SOP_MASTER.exists():
            out.append((firm_master_rel, FIRM_SOP_MASTER.read_text(encoding="utf-8")))

    # Runbooks appear at the front of the VENDOR PDF only. They're
    # incident-time material for the operator; the firm-facing SOP
    # doesn't include them (firms don't run their own operator
    # procedures).
    if is_vendor and RUNBOOKS_DIR.exists():
        runbook_dir_rel = RUNBOOKS_DIR.relative_to(REPO).as_posix()
        for name in RUNBOOKS_ORDER:
            p = RUNBOOKS_DIR / name
            if p.exists():
                out.append((f"{runbook_dir_rel}/{name}", p.read_text(encoding="utf-8")))
            else:
                print(f"  WARN: missing runbook {runbook_dir_rel}/{name}", file=sys.stderr)

    # Firm chapters. Always included in the firm PDF (the only chapter
    # set). Also included verbatim in the vendor PDF so on-site
    # engineers have every firm-side procedure in one document — and
    # cross-references between firm and vendor chapters all resolve.
    firm_dir_rel = FIRM_SOP_DIR.relative_to(REPO).as_posix()
    for name in FIRM_CHAPTER_ORDER:
        p = FIRM_SOP_DIR / name
        if p.exists():
            out.append((f"{firm_dir_rel}/{name}", p.read_text(encoding="utf-8")))
        elif not is_vendor:
            print(f"  WARN: missing chapter {firm_dir_rel}/{name}", file=sys.stderr)

    # Vendor-only chapters next.
    if is_vendor:
        vendor_dir_rel = VENDOR_SOP_DIR.relative_to(REPO).as_posix()
        for name in VENDOR_CHAPTER_ORDER:
            p = VENDOR_SOP_DIR / name
            if p.exists():
                out.append((f"{vendor_dir_rel}/{name}", p.read_text(encoding="utf-8")))
            else:
                print(f"  WARN: missing vendor chapter {vendor_dir_rel}/{name}", file=sys.stderr)

    # Appendices last. Bundled in BOTH firm and vendor PDFs so the
    # ~10 cross-refs from SOP chapters to iso27001-controls.md /
    # qdrant-ha.md / syncthing-setup.md / DPA_DRAFT.md resolve as
    # in-PDF anchor jumps instead of dead relative paths.
    for path, _title in APPENDIX_FILES:
        if path.exists():
            rel = path.relative_to(REPO).as_posix()
            out.append((rel, path.read_text(encoding="utf-8")))
    return out


def slugify(text: str) -> str:
    """GitHub-style anchor slug for headings."""
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s


def remove_frontmatter(md: str) -> str:
    """Strip a top-of-file H1 if present so chapter divs control headings."""
    return md  # we keep H1s; the chapter wrapper handles spacing


def build_html(chapters: list[tuple[str, str]]) -> str:
    import markdown
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md_ext = ["fenced_code", "tables", "toc", "attr_list", "sane_lists"]

    master_rel = SOP_MASTER.relative_to(REPO).as_posix()
    cover = f"""
    <div class="cover">
      <h1>LocallyAI</h1>
      <div class="subtitle">{SOP_SUBTITLE}</div>
      <div class="meta">
        Generated {today}<br/>
        From git ref: {git_ref()}<br/>
        Read order matches the master index in {master_rel}
      </div>
    </div>
    """

    toc_items = []
    for path, _ in chapters:
        title = pretty_title(path)
        toc_items.append(f'<li><a href="#{file_anchor(path)}">{title}</a> — <code>{path}</code></li>')
    toc = f"""
    <div class="toc">
      <h1>Table of contents</h1>
      <ol>{''.join(toc_items)}</ol>
    </div>
    """

    # Lookup of REPO-relative path → file anchor. Used by the link
    # rewriter to turn `[text](other.md#heading)` references into
    # in-PDF anchor jumps. Heading anchors inside each chapter are also
    # namespaced (prefixed with the chapter's file_anchor) so two
    # chapters with the same heading don't collide.
    path_to_anchor = {path: file_anchor(path) for path, _ in chapters}

    chapter_html_blocks = []
    for path, md_text in chapters:
        text = remove_frontmatter(md_text)
        body = markdown.markdown(text, extensions=md_ext, output_format="html5")
        anchor = file_anchor(path)
        body = _namespace_heading_ids(body, anchor)
        body = _rewrite_cross_references(body, source_path=path,
                                         path_to_anchor=path_to_anchor)
        chapter_html_blocks.append(
            f'<div class="chapter" id="{anchor}">{body}</div>'
        )

    css = PRINT_CSS.replace("__SOP_TITLE__", SOP_TITLE)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{SOP_TITLE}</title>
<style>{css}</style>
</head>
<body>
{cover}
{toc}
{''.join(chapter_html_blocks)}
</body>
</html>
"""


# ── Cross-reference rewriting ────────────────────────────────────────────────
# In source markdown, chapters cross-link using `[text](other.md#heading)`.
# In a single-file PDF those relative file links are broken. We rewrite each
# inline cross-reference to point at an in-PDF anchor:
#   - `[t](other.md)`              → `<a href="#{file_anchor('docs/sop/other.md')}">t</a>`
#   - `[t](other.md#heading-slug)` → `<a href="#{file_anchor(other)}--heading-slug">t</a>`
# Heading IDs inside each chapter are pre-prefixed with the chapter's
# file_anchor so the second form resolves uniquely across the document.

_HEADING_ID_RE = re.compile(r'(<h[1-6]\b[^>]*?\bid=")([^"]+)("[^>]*>)', re.IGNORECASE)
_ANCHOR_HREF_RE = re.compile(
    r'(<a\b[^>]*?\bhref=")([^"#][^"#]*?\.md)(#[^"]*)?("[^>]*>)',
    re.IGNORECASE,
)


def _namespace_heading_ids(html: str, chapter_anchor: str) -> str:
    """Prefix every <hN id="X"> with the chapter anchor so anchors are
    globally unique across chapters that happen to share heading names
    (e.g. several chapters have ## Procedure)."""
    def _sub(m):
        return f'{m.group(1)}{chapter_anchor}--{m.group(2)}{m.group(3)}'
    return _HEADING_ID_RE.sub(_sub, html)


def _resolve_relative(source_path: str, target_md: str) -> str | None:
    """Resolve `target_md` (e.g. 'other.md' or '../runbooks/x.md') against
    the directory of `source_path` (a REPO-relative path like
    'docs/sop/compliance.md'). Returns the normalised REPO-relative path
    or None if it doesn't normalise inside REPO."""
    try:
        source_dir = (REPO / source_path).resolve().parent
        target_abs = (source_dir / target_md).resolve()
        return target_abs.relative_to(REPO).as_posix()
    except Exception:
        return None


def _rewrite_cross_references(html: str, source_path: str,
                              path_to_anchor: dict[str, str]) -> str:
    """Rewrite `<a href="other.md[#heading]">` in this chapter's HTML to
    point at the in-PDF anchor for the target chapter (and heading).

    Links to chapters not in the PDF bundle (e.g. firm PDF referencing a
    runbook) are left untouched and a warning is logged."""
    unresolved: set[str] = set()

    def _sub(m):
        prefix, md_path, fragment, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        resolved = _resolve_relative(source_path, md_path)
        if resolved is None or resolved not in path_to_anchor:
            # Not in this PDF — leave the href as-is, warn once.
            unresolved.add(f"{source_path} → {md_path}")
            return m.group(0)
        target_anchor = path_to_anchor[resolved]
        if fragment:
            # `[t](other.md#heading-slug)` — the heading inside `other.md`
            # was namespaced by _namespace_heading_ids() to
            # `{target_anchor}--{heading-slug}`.
            heading_slug = fragment[1:]  # strip the leading '#'
            new_href = f'#{target_anchor}--{heading_slug}'
        else:
            new_href = f'#{target_anchor}'
        return f'{prefix}{new_href}{suffix}'

    new_html = _ANCHOR_HREF_RE.sub(_sub, html)
    for ref in sorted(unresolved):
        print(f"  WARN: cross-reference not in bundle: {ref}", file=sys.stderr)
    return new_html


def file_anchor(path: str) -> str:
    return slugify(path.replace("/", "-").replace(".md", ""))


def pretty_title(path: str) -> str:
    name = path.rsplit("/", 1)[-1].replace(".md", "")
    overrides = {
        "SOP": "Master index",
        "VENDOR_SOP": "Master index (vendor-internal)",
        "CHANGELOG": "CHANGELOG",
        "vendor-team": "V1 — Vendor team & succession",
        "vendor-infrastructure": "V2 — Vendor infrastructure inventory",
        "vendor-daily-ops": "V3 — Daily vendor ops",
        "vendor-release-engineering": "V4 — Release engineering",
        "vendor-incidents-own-infra": "V5 — Vendor-side incidents",
        "vendor-disaster-recovery": "V6 — Disaster recovery",
        "vendor-sales": "V7 — Sales pipeline",
        "vendor-onboarding": "V8 — Onboarding playbook",
        "vendor-customer-success": "V9 — Customer success cadence",
        "vendor-sub-processors": "V10 — Sub-processor management",
        "vendor-compliance": "V11 — Vendor compliance",
        "vendor-people": "V12 — People (hire, onboard, offboard)",
        "vendor-internal-dryrun": "V13 — Internal dry-run / dogfood onboarding",
        "repo-access": "Repository access — SSH deploy keys",
        "setup-mac-single": "Setup — Mac single-node",
        "setup-mac-ha": "Setup — Mac 2-node HA",
        "setup-windows": "Setup — Windows",
        "setup-saudi": "Setup — Saudi (KSA / PDPL)",
        "daily": "Daily operations",
        "bulk-ingest": "Bulk corpus ingestion",
        "one-click-start": "One-click start & stop",
        "client-install": "Client app install (staff laptops)",
        "updates": "System updates (releases + LLM picker)",
        "data-isolation": "Data isolation (per-firm + egress allowlist)",
        "vendor-monitoring": "Vendor monitoring (dashboard + 4-hour SLA)",
        "onboarding": "Vendor onboarding intake (firm profile collection)",
        "remote-access": "Remote staff access (Tailscale / VPN / CF Tunnel)",
        "install-checklist": "Install checklist (engineer on-site)",
        "maintenance": "Maintenance",
        "compliance": "Compliance ops",
        "compliance-saudi": "Compliance ops — Saudi (PDPL)",
        "dpo-compliance-portal": "DPO compliance portal (Manager UI reference)",
        "sizing": "Sizing — per-firm hardware + model recommendation",
        "document-acl": "Document access control (per-doc ACL)",
        "ha-architecture": "HA architecture — two-Mac active/standby pair",
        "dms-integration": "DMS integration (design + phased plan)",
        "failover-test": "Runbook — quarterly failover-readiness test",
        "rotate-deploy-keys-pat": "Runbook — rotate GitHub deploy-keys PAT",
        "iso27001-controls": "Appendix A — ISO 27001:2022 controls map",
        "qdrant-ha": "Appendix B — Qdrant HA setup",
        "syncthing-setup": "Appendix C — Syncthing setup",
        "DPA_DRAFT": "Appendix D — DPA template (UK)",
        "DPA_DRAFT_SA": "Appendix E — DPA template (KSA / PDPL)",
        "incidents-software": "Incidents — software",
        "incidents-physical": "Incidents — physical / environment",
        "incidents-security": "Incidents — security",
        "incidents-operator": "Incidents — operator error",
        "incidents-people": "Incidents — people",
        "incidents-legal": "Incidents — legal & regulatory",
        "incidents-misuse": "Incidents — misuse / insider",
        "incidents-service": "Incidents — service quality",
        "incidents-supply": "Incidents — supply chain & upstream",
        "scale-out": "Scale-out & migration",
        "recovery": "Recovery & DR",
        "decommission": "Decommission",
    }
    return overrides.get(name, name)


def git_ref() -> str:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True).strip()
        try:
            tag = subprocess.check_output(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
            return f"{sha} (tag {tag})"
        except subprocess.CalledProcessError:
            return sha
    except Exception:
        return "(no git)"


def find_chrome() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        # Windows guesses (run from WSL or Git Bash):
        "/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    found = shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")
    return found


def render_with_chrome(html_path: Path, pdf_path: Path) -> None:
    chrome = find_chrome()
    if not chrome:
        sys.exit(
            "Chrome / Chromium not found. Install Chrome from https://www.google.com/chrome/ "
            "or set CHROME=/path/to/chrome and re-run.")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        "--virtual-time-budget=10000",
        f"--print-to-pdf={pdf_path}",
        "--print-to-pdf-no-header",
        f"file://{html_path.absolute()}",
    ]
    print(f"  → Chrome render…")
    subprocess.run(cmd, check=True, capture_output=True)


def render_with_pandoc(html_path: Path, pdf_path: Path) -> bool:
    """Try pandoc with --pdf-engine=wkhtmltopdf if both are present.
    Returns True on success; False if either tool missing."""
    if not (shutil.which("pandoc") and (shutil.which("wkhtmltopdf") or shutil.which("weasyprint"))):
        return False
    engine = "weasyprint" if shutil.which("weasyprint") else "wkhtmltopdf"
    cmd = ["pandoc", str(html_path), "-o", str(pdf_path), f"--pdf-engine={engine}"]
    print(f"  → pandoc + {engine}…")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  pandoc failed: {e.stderr.decode()[:200]}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None,
                    help="Output PDF path; default dist/<prefix>-<git>-<utc>.pdf")
    ap.add_argument("--prefer", choices=("chrome", "pandoc"), default="chrome")
    ap.add_argument("--vendor", action="store_true",
                    help="Build the vendor-internal SOP (default: firm-facing SOP)")
    args = ap.parse_args()

    # Switch to vendor-mode if --vendor flag is set.
    if args.vendor:
        global SOP_MASTER, SOP_DIR, CHAPTER_ORDER, SOP_TITLE, SOP_SUBTITLE, OUTPUT_PREFIX
        SOP_MASTER = VENDOR_SOP_MASTER
        SOP_DIR = VENDOR_SOP_DIR
        CHAPTER_ORDER = VENDOR_CHAPTER_ORDER
        SOP_TITLE = "LocallyAI Vendor SOP"
        SOP_SUBTITLE = "Vendor-Internal Standard Operating Procedure"
        OUTPUT_PREFIX = "locallyai-vendor-sop"

    chapters = read_chapters()
    print(f"Building {SOP_TITLE} PDF from {len(chapters)} files…")
    html = build_html(chapters)

    dist = REPO / "dist"
    dist.mkdir(exist_ok=True)
    if args.out:
        pdf_path = Path(args.out)
    else:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M")
        pdf_path = dist / f"{OUTPUT_PREFIX}-{git_ref().split()[0]}-{stamp}.pdf"

    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "sop.html"
        html_path.write_text(html, encoding="utf-8")

        ok = False
        if args.prefer == "pandoc":
            ok = render_with_pandoc(html_path, pdf_path)
        if not ok:
            render_with_chrome(html_path, pdf_path)

    size_mb = pdf_path.stat().st_size / 1024 / 1024
    print(f"✔ {pdf_path}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
