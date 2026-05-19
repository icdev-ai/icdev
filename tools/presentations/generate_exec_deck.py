"""
ICDEV(tm) Executive Presentation Generator  v2 — developer-first narrative
Produces: tools/presentations/output/ICDEV_Executive_Deck.pptx
Message: A System That Builds Systems. Canvases, Workflows, Digital Twin, Use Cases.
Compliance / ATO = silent byproduct, never the headline.
"""

from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# ── Palette ─────────────────────────────────────────────────────────────────
NAVY  = RGBColor(0x0A, 0x16, 0x28)
GOLD  = RGBColor(0xC8, 0xA9, 0x51)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LGRAY = RGBColor(0xE0, 0xE6, 0xF0)
MGRAY = RGBColor(0x1E, 0x3A, 0x5F)
GREEN = RGBColor(0x2E, 0xCC, 0x71)
BLUE  = RGBColor(0x2E, 0x86, 0xC1)
RED   = RGBColor(0xE7, 0x4C, 0x3C)
TEAL  = RGBColor(0x17, 0xA5, 0x8A)

W  = Inches(13.33)
H  = Inches(7.50)
LM = Inches(0.55)
CW = W - LM * 2


# ── Primitives ───────────────────────────────────────────────────────────────

def _new_prs():
    prs = Presentation()
    prs.slide_width  = W
    prs.slide_height = H
    return prs

def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])

def _bg(slide, color=NAVY):
    f = slide.background.fill
    f.solid()
    f.fore_color.rgb = color

def _rect(slide, l, t, w, h, fill, line=None, lw=Pt(1)):
    s = slide.shapes.add_shape(1, l, t, w, h)
    s.fill.solid(); s.fill.fore_color.rgb = fill
    if line: s.line.color.rgb = line; s.line.width = lw
    else: s.line.fill.background()
    return s

def _box(slide, l, t, w, h, text="", size=14, bold=False,
         italic=False, color=WHITE, align=PP_ALIGN.LEFT, wrap=True):
    shape = slide.shapes.add_textbox(l, t, w, h)
    tf = shape.text_frame; tf.word_wrap = wrap
    p = tf.paragraphs[0]; p.alignment = align
    run = p.add_run(); run.text = text
    run.font.size = Pt(size); run.font.bold = bold
    run.font.italic = italic; run.font.color.rgb = color
    return tf

def _gold_bar(slide, top=Inches(1.55), h=Inches(0.04)):
    _rect(slide, LM, top, CW, h, GOLD)

def _footer(slide, n, text="ICDEV™  ·  A System That Builds Systems"):
    _box(slide, LM, H - Inches(0.32), CW - Inches(0.6), Inches(0.28),
         text, size=8, color=MGRAY)
    _box(slide, W - Inches(0.8), H - Inches(0.32), Inches(0.7), Inches(0.28),
         str(n), size=9, color=MGRAY, align=PP_ALIGN.RIGHT)

def _title(slide, title, sub=""):
    _box(slide, LM, Inches(0.35), CW, Inches(1.1),
         title, size=32, bold=True, color=GOLD)
    _gold_bar(slide)
    if sub:
        _box(slide, LM, Inches(1.65), CW, Inches(0.42),
             sub, size=13, color=LGRAY, italic=True)

def _notes(slide, text):
    tf = slide.notes_slide.notes_text_frame
    tf.text = text

def _card(slide, l, t, w, h, heading, body, hc=GOLD, bc=LGRAY, hs=12, bs=10, border=GOLD):
    _rect(slide, l, t, w, h, MGRAY, border)
    pad = Inches(0.12)
    _box(slide, l+pad, t+pad, w-pad*2, Inches(0.38),
         heading, size=hs, bold=True, color=hc)
    _box(slide, l+pad, t+Inches(0.42), w-pad*2, h-Inches(0.54),
         body, size=bs, color=bc, wrap=True)


# ── Slides ───────────────────────────────────────────────────────────────────

def s01_cover(prs):
    s = _blank(prs); _bg(s)
    _rect(s, 0, 0, W, Inches(0.1), GOLD)
    _rect(s, 0, H-Inches(0.1), W, Inches(0.1), GOLD)

    _box(s, LM, Inches(0.9), CW, Inches(1.9),
         "A System That\nBuilds Systems.",
         size=52, bold=True, color=GOLD, align=PP_ALIGN.CENTER)

    _box(s, LM, Inches(2.85), CW, Inches(0.55),
         "ICDEV™  ·  The Developer Platform for Government AI",
         size=18, color=WHITE, align=PP_ALIGN.CENTER)

    _rect(s, Inches(3.8), Inches(3.55), Inches(5.73), Inches(0.03), GOLD)

    _box(s, LM, Inches(3.7), CW, Inches(0.45),
         "Adopt vs. Build  ·  Executive Briefing  ·  May 2026",
         size=12, color=LGRAY, align=PP_ALIGN.CENTER, italic=True)

    stats = [
        ("12", "Design\nCanvases"),
        ("15", "AI Agents"),
        ("530+", "Deterministic\nTools"),
        ("6", "Supported\nLanguages"),
        ("100+", "Developers\nTrained Year‑1"),
    ]
    sw = CW / len(stats) - Inches(0.08)
    for i, (num, lbl) in enumerate(stats):
        x = LM + i*(sw+Inches(0.08))
        _rect(s, x, Inches(4.3), sw, Inches(1.8), MGRAY, GOLD)
        _box(s, x, Inches(4.38), sw, Inches(0.75),
             num, size=30, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
        _box(s, x, Inches(5.12), sw, Inches(0.75),
             lbl, size=10, color=LGRAY, align=PP_ALIGN.CENTER)

    _box(s, LM, Inches(6.4), CW, Inches(0.35),
         "Compliance artifacts generated automatically. ATO is the byproduct, not the goal.",
         size=10, color=MGRAY, align=PP_ALIGN.CENTER, italic=True)


def s02_developer_reality(prs):
    s = _blank(prs); _bg(s)
    _title(s, "What Developers Are Dealing With Today",
           "Before they write a single line of mission code.")
    _footer(s, 2)

    problems = [
        ("Months of setup before the first feature",
         "Infrastructure provisioning, compliance scaffolding, CI/CD pipeline wiring, "
         "dependency audits — all before a single line of mission code is written. "
         "Developers spend more time getting ready to build than actually building."),
        ("Knowledge that lives in people, not systems",
         "Security architect, ISSO, infrastructure engineer, CI/CD expert — remove any one "
         "person and the program stalls. Every team re-invents the same wheel on every program."),
        ("Complexity that grows faster than teams can absorb",
         "Modern government software requires multi-cloud IaC, AI governance, supply chain integrity, "
         "zero-trust architecture, and six languages — simultaneously. "
         "No individual team can master all of it."),
    ]

    y = Inches(2.1)
    for heading, body in problems:
        _rect(s, LM, y, CW, Inches(1.4), MGRAY, GOLD)
        _box(s, LM+Inches(0.18), y+Inches(0.1), CW-Inches(0.36), Inches(0.42),
             heading, size=15, bold=True, color=GOLD)
        _box(s, LM+Inches(0.18), y+Inches(0.52), CW-Inches(0.36), Inches(0.78),
             body, size=12, color=LGRAY, wrap=True)
        y += Inches(1.55)

    _box(s, LM, Inches(6.75), CW, Inches(0.35),
         "The problem is not that developers lack skill. The problem is that they lack a platform.",
         size=12, bold=True, color=GOLD, align=PP_ALIGN.CENTER)


def s03_the_vision(prs):
    s = _blank(prs); _bg(s)
    _footer(s, 3)

    _box(s, LM, Inches(0.45), CW, Inches(1.4),
         "What if your platform could build\nyour next platform?",
         size=38, bold=True, color=GOLD, align=PP_ALIGN.CENTER)

    _gold_bar(s, Inches(1.95), Inches(0.04))

    _box(s, LM, Inches(2.1), CW, Inches(0.55),
         "ICDEV™ is a meta-builder. It generates complete, working systems "
         "from plain English — systems that themselves can build features using the same engine.",
         size=14, color=LGRAY, align=PP_ALIGN.CENTER, wrap=True)

    flow = [
        ("Plain English\nRequirements", LGRAY),
        ("12 Design\nCanvases", GOLD),
        ("FORGE\nWorkflow", BLUE),
        ("ANVIL\nBuild Cycle", GREEN),
        ("Working\nSystem", GOLD),
    ]
    fw = (CW - Inches(0.6)) / len(flow)
    y = Inches(2.85)
    x = LM
    for label, clr in flow:
        _rect(s, x, y, fw, Inches(1.35), MGRAY, clr)
        _box(s, x, y+Inches(0.3), fw, Inches(0.75),
             label, size=13, bold=True, color=clr, align=PP_ALIGN.CENTER)
        x += fw
        if x < LM + CW - fw:
            _box(s, x, y+Inches(0.48), Inches(0.15), Inches(0.45),
                 "→", size=18, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
            x += Inches(0.15)

    three = [
        ("Canvases are\nyour design environment",
         "12 visual builders where developers think, design, "
         "and execute across every domain — network, security, data, AI, pipeline, and more."),
        ("Digital Twin\nsimulates the outcome",
         "Before writing a line of code, run a 6-dimension simulation. "
         "Get three Courses of Action with cost, schedule, and risk projections."),
        ("Use Cases\nlaunch in minutes",
         "Pre-built expert workflows for real government problems — "
         "modernization, budget execution, doc refresh — with canvas wiring done for you."),
    ]
    tw = (CW - Inches(0.2)) / 3
    y = Inches(4.45)
    for i, (heading, body) in enumerate(three):
        x = LM + i*(tw+Inches(0.1))
        _rect(s, x, y, tw, Inches(2.3), MGRAY, [GOLD, BLUE, TEAL][i])
        _box(s, x+Inches(0.12), y+Inches(0.12), tw-Inches(0.24), Inches(0.6),
             heading, size=13, bold=True, color=[GOLD, BLUE, TEAL][i])
        _box(s, x+Inches(0.12), y+Inches(0.75), tw-Inches(0.24), Inches(1.38),
             body, size=10, color=LGRAY, wrap=True)


def s04_forge(prs):
    s = _blank(prs); _bg(s)
    _title(s, "FORGE — The Engine Underneath",
           "Six layers that separate what to build from how to build it.")
    _footer(s, 4)

    layers = [
        ("Goals",        "What to achieve.\nWhich tools. Expected\noutputs. 66 workflows."),
        ("Orchestration","AI reasons here.\nDecides tool order.\nHandles errors."),
        ("Tools",        "530+ Python scripts.\nOne job each.\nFully deterministic."),
        ("Args",         "YAML/JSON config.\nChange behavior\nwithout editing code."),
        ("Context",      "Static reference.\nCompliance catalogs.\nTone and standards."),
        ("Hard Prompts", "Reusable LLM\ninstruction templates\nacross all workflows."),
    ]
    lw = (CW - Inches(0.25)) / len(layers)
    y = Inches(2.05)
    for i, (name, desc) in enumerate(layers):
        x = LM + i*(lw+Inches(0.05))
        _rect(s, x, y, lw, Inches(2.9), MGRAY, GOLD)
        _box(s, x+Inches(0.08), y+Inches(0.12), lw-Inches(0.16), Inches(0.5),
             name, size=13, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
        _rect(s, x+Inches(0.1), y+Inches(0.65), lw-Inches(0.2), Inches(0.03), GOLD)
        _box(s, x+Inches(0.08), y+Inches(0.75), lw-Inches(0.16), Inches(1.95),
             desc, size=10, color=LGRAY, align=PP_ALIGN.CENTER)

    _box(s, LM, Inches(5.15), CW, Inches(0.5),
         "Why this matters", size=13, bold=True, color=GOLD)
    _box(s, LM, Inches(5.68), CW, Inches(0.9),
         "LLMs are probabilistic. At 90% accuracy per step, a 5-step workflow degrades to ~59% end-to-end. "
         "FORGE confines AI reasoning to orchestration only. Every tool call is deterministic Python. "
         "Developers get reliable, repeatable, auditable delivery — not prompt roulette.",
         size=12, color=LGRAY, wrap=True)


def s05_anvil(prs):
    s = _blank(prs); _bg(s)
    _title(s, "ANVIL — Every Feature, Fully Assembled",
           "Five-phase build cycle. Runs automatically on every feature request.")
    _footer(s, 5)

    phases = [
        ("Architect",  "Design and acceptance\ncriteria defined up\nfront. No surprises.", BLUE),
        ("Navigate",   "Map to existing tools\nand patterns. Reuse\nbefore you build.", BLUE),
        ("Verify",     "Failing tests written\nfirst. The spec IS\nthe test. (RED)", RED),
        ("Integrate",  "Implementation\ngenerated until every\ntest passes. (GREEN)", GREEN),
        ("Launch",     "Refactor, scan,\ncompliance map,\nmerge to main.", GOLD),
    ]
    pw = (CW - Inches(0.6)) / len(phases)
    y = Inches(2.1)
    for i, (name, desc, clr) in enumerate(phases):
        x = LM + i*(pw+Inches(0.15))
        _rect(s, x, y, pw, Inches(3.2), MGRAY, clr)
        _box(s, x, y+Inches(0.12), pw, Inches(0.5),
             name, size=16, bold=True, color=clr, align=PP_ALIGN.CENTER)
        _rect(s, x+Inches(0.1), y+Inches(0.65), pw-Inches(0.2), Inches(0.03), clr)
        _box(s, x+Inches(0.08), y+Inches(0.78), pw-Inches(0.16), Inches(2.1),
             desc, size=11, color=LGRAY, align=PP_ALIGN.CENTER)
        if i < 4:
            _box(s, x+pw+Inches(0.02), y+Inches(1.45), Inches(0.12), Inches(0.4),
                 "→", size=16, bold=True, color=GOLD, align=PP_ALIGN.CENTER)

    outputs = [
        "Working, tested code in 6 languages",
        "Security scan report (SAST, deps, secrets)",
        "SBOM and compliance artifacts — automatically",
        "CI/CD pipeline wired and passing",
        "Deployment-ready container image",
    ]
    _box(s, LM, Inches(5.55), CW, Inches(0.38),
         "Every ANVIL run produces:", size=12, bold=True, color=GOLD)
    ow = CW / len(outputs)
    for i, out in enumerate(outputs):
        _box(s, LM+i*ow, Inches(5.95), ow-Inches(0.05), Inches(0.55),
             f"▸  {out}", size=10, color=LGRAY)


def s06_canvases_overview(prs):
    s = _blank(prs); _bg(s)
    _title(s, "12 Design Canvases — Your Thinking Environment",
           "Design visually. Query in plain English. Execute with a click.")
    _footer(s, 6)

    canvases = [
        ("NDC",  "Network Design",        "Topology, multi-cloud, compliance overlay, cost modeling"),
        ("SDC",  "Security Design",       "Threat modeling, ATT&CK mapping, gap detection, auto-fix"),
        ("PDC",  "Pipeline Design",       "Visual CI/CD, SLSA, OWASP coverage, cost estimate"),
        ("BDC",  "Boundary Design",       "System boundary, interface agreements, configuration control"),
        ("DDC",  "Data Design",           "Classification zones, PII/CUI tracking, data lineage"),
        ("ODC",  "Observability Design",  "Detection coverage, Sigma rules, alerting topology"),
        ("IDC",  "Infrastructure Design", "IaC resources, 6 cloud providers, Terraform generation"),
        ("AADC", "Agentic AI Design",     "7 solution packs, 40+ node types, AI risk register"),
        ("QDC",  "Quality Design",        "Code quality gates, test coverage, maintainability scoring"),
        ("MDC",  "Migration Design",      "7R assessment, strangler fig tracking, cutover planning"),
        ("AIMC", "AI/ML Model Design",    "Foundation model catalog, deployment patterns, IL levels"),
        ("MSN",  "Mission Canvas",        "Strategic planning, operational design, COA visualization"),
    ]
    cols, bw = 4, (CW - Inches(0.3)) / 4
    bh = Inches(1.28)
    y0 = Inches(2.05)
    for i, (code, name, desc) in enumerate(canvases):
        col, row = i % cols, i // cols
        x = LM + col*(bw+Inches(0.1))
        y = y0 + row*(bh+Inches(0.08))
        _rect(s, x, y, bw, bh, MGRAY, GOLD)
        _box(s, x+Inches(0.1), y+Inches(0.08), bw-Inches(0.2), Inches(0.38),
             f"{code}  ·  {name}", size=11, bold=True, color=GOLD)
        _box(s, x+Inches(0.1), y+Inches(0.5), bw-Inches(0.2), Inches(0.68),
             desc, size=9, color=LGRAY, wrap=True)

    _box(s, LM, Inches(6.72), CW, Inches(0.35),
         "Every canvas: natural-language IQE queries · knowledge graph indexing · "
         "simulation-ready · air-gap safe",
         size=10, color=MGRAY, align=PP_ALIGN.CENTER)


def s07_canvas_in_action(prs):
    s = _blank(prs); _bg(s)
    _title(s, "A Canvas in Action — Network Design (NDC)",
           "From blank topology to compliant architecture in one session.")
    _footer(s, 7)

    steps = [
        ("1  Drag + Drop",
         "Developer places cloud resources on the canvas: VPCs, subnets, load balancers, "
         "managed services. No Terraform yet. Just thinking visually.",
         BLUE),
        ("2  Ask a Question",
         '"What\'s my blast radius if the east subnet goes down?"  '
         "IQE runs a natural language query over the live diagram. Answer in <1 second.",
         TEAL),
        ("3  Run a Simulation",
         "Click Simulate. Digital Twin scores the design across architecture, cost, and risk dimensions. "
         "Recommendations surface inline.",
         GOLD),
        ("4  Export + Execute",
         "Export to Terraform, Ansible, or K8s manifests with one click. "
         "ANVIL wires the CI/CD pipeline. The canvas becomes the system.",
         GREEN),
    ]
    sw = (CW - Inches(0.3)) / 4
    y = Inches(2.1)
    for i, (title, body, clr) in enumerate(steps):
        x = LM + i*(sw+Inches(0.1))
        _rect(s, x, y, sw, Inches(3.5), MGRAY, clr)
        _box(s, x+Inches(0.1), y+Inches(0.12), sw-Inches(0.2), Inches(0.5),
             title, size=13, bold=True, color=clr)
        _rect(s, x+Inches(0.1), y+Inches(0.65), sw-Inches(0.2), Inches(0.03), clr)
        _box(s, x+Inches(0.1), y+Inches(0.78), sw-Inches(0.2), Inches(2.55),
             body, size=10, color=LGRAY, wrap=True)

    _box(s, LM, Inches(5.8), CW, Inches(0.45),
         "Same pattern works across all 12 canvases.", size=14, bold=True,
         color=GOLD, align=PP_ALIGN.CENTER)
    _box(s, LM, Inches(6.3), CW, Inches(0.55),
         "Design once on the canvas — ICDEV™ generates IaC, pipeline config, test scaffolding, "
         "and compliance evidence automatically. Developers ship. The platform handles the rest.",
         size=11, color=LGRAY, align=PP_ALIGN.CENTER, wrap=True)


def s08_chat_use_cases(prs):
    s = _blank(prs); _bg(s)
    _title(s, "Unified Chat — Intent Becomes Software",
           "Not a chatbot. A structured intake engine that wires canvases, agents, and tools for you.")
    _footer(s, 8)

    # 3-pane mockup
    lw, cw2, rw = Inches(2.7), Inches(5.5), Inches(3.6)
    ph, py = Inches(3.9), Inches(2.1)
    gap = Inches(0.12)
    xl, xc, xr = LM, LM+lw+gap, LM+lw+gap+cw2+gap

    _rect(s, xl, py, lw, ph, MGRAY, GOLD)
    _rect(s, xc, py, cw2, ph, MGRAY, BLUE)
    _rect(s, xr, py, rw, ph, MGRAY, TEAL)

    _box(s, xl+Inches(0.1), py+Inches(0.1), lw-Inches(0.2), Inches(0.38),
         "Use Cases (13)", size=11, bold=True, color=GOLD)
    sidebar_cases = [
        ("+ New Chat",               GOLD),
        ("▸ General Modernization",  GOLD),
        ("▸ Year-End Budget Sprint", LGRAY),
        ("▸ Doc Refresh",            LGRAY),
        ("▸ ATO Package Builder",    LGRAY),
        ("▸ CDRL Generator",         LGRAY),
        ("▸ Incident Response Plan", LGRAY),
        ("▸ SBOM Attestation",       LGRAY),
        ("▸ +5 more...",             MGRAY),
    ]
    for j, (uc, clr) in enumerate(sidebar_cases):
        _box(s, xl+Inches(0.12), py+Inches(0.55)+j*Inches(0.36),
             lw-Inches(0.24), Inches(0.34), uc, size=9, color=clr)

    _box(s, xc+Inches(0.12), py+Inches(0.1), cw2-Inches(0.24), Inches(0.38),
         "Message Stream", size=11, bold=True, color=BLUE)
    for j, (who, msg) in enumerate([
        ("You",    "We have a COBOL system that needs modernization."),
        ("ICDEV",  "Got it. I've pre-loaded 12 modernization requirements and wired the Migration "
                   "Canvas + Digital Twin. Readiness score: 78%. Shall I run a simulation?"),
        ("You",    "Yes, show me the three options."),
        ("ICDEV",  "Running Digital Twin — 3 COAs ready. Speed: 1 PI. Balanced: 2 PI (recommended). "
                   "Comprehensive: 4 PI. Sending to canvas now."),
    ]):
        c = GOLD if who == "You" else TEAL
        _box(s, xc+Inches(0.12), py+Inches(0.55)+j*Inches(0.75),
             cw2-Inches(0.24), Inches(0.22), who, size=9, bold=True, color=c)
        _box(s, xc+Inches(0.12), py+Inches(0.77)+j*Inches(0.75),
             cw2-Inches(0.24), Inches(0.45), msg, size=9, color=LGRAY, wrap=True)

    _box(s, xr+Inches(0.12), py+Inches(0.1), rw-Inches(0.24), Inches(0.38),
         "Live Context", size=11, bold=True, color=TEAL)
    for j, item in enumerate(["Readiness: 78 / 100",
                               "Requirements: 12 loaded",
                               "Migration Canvas: active",
                               "Digital Twin: ready",
                               "Supply Chain: queued",
                               "————————",
                               "AI Governance: active",
                               "Transparency: OK",
                               "Fairness: OK"]):
        _box(s, xr+Inches(0.12), py+Inches(0.55)+j*Inches(0.37),
             rw-Inches(0.24), Inches(0.34), item, size=9, color=LGRAY)

    callouts = [
        ("Expert persona loaded automatically",
         "Each use case launches with a specialized AI persona and pre-seeded requirements."),
        ("Canvases wired on launch",
         "Clicking a use case pre-configures Migration Canvas, Digital Twin, Supply Chain in one step."),
        ("Readiness score keeps you honest",
         "7-dimension scoring surfaces gaps before you build, not after you deploy."),
    ]
    cw3 = CW / 3 - Inches(0.1)
    y = Inches(6.15)
    for i, (title, body) in enumerate(callouts):
        x = LM + i*(cw3+Inches(0.1))
        _box(s, x, y, cw3, Inches(0.28), title, size=10, bold=True, color=GOLD)
        _box(s, x, y+Inches(0.28), cw3, Inches(0.42), body, size=9, color=LGRAY, wrap=True)


def s09_use_cases(prs):
    s = _blank(prs); _bg(s)
    _title(s, "13 Use Cases. Real Problems. Minutes to Launch.",
           "Pre-built expert workflows — requirements loaded, canvases wired, AI persona ready.")
    _footer(s, 9)

    # Featured 3 (shorter cards to leave room for the full catalog below)
    cases = [
        ("General Modernization",
         "12 requirements pre-loaded",
         [
             "Describe your legacy system in plain English.",
             "ICDEV wires Migration Canvas + Digital Twin.",
             "3 COAs: Speed / Balanced / Comprehensive.",
             "7Rs classification: Retire → Re-architect.",
             "Output: migration plan with IaC and timeline.",
         ],
         GOLD),
        ("Year-End Budget Sprint",
         "14 requirements pre-loaded",
         [
             "What do you need to spend before FY-end?",
             "ICDEV generates BOM, IGCE, and SOW in minutes.",
             "FAR/DFARS + Section 889 checked automatically.",
             "Digital Twin projects budget vs. outcome.",
             "Output: CSV/XLSX BOM with contract vehicles.",
         ],
         BLUE),
        ("Crowd-Sourced Doc Refresh",
         "10 requirements pre-loaded",
         [
             "Which document is stale?",
             "Staleness detected: dates, deprecated references.",
             "Role-weighted crowd voting on edits.",
             "AI rebuilds approved sections.",
             "Output: version-controlled doc with audit trail.",
         ],
         TEAL),
    ]

    cw2 = (CW - Inches(0.2)) / 3
    for i, (title, badge, items, clr) in enumerate(cases):
        x = LM + i*(cw2+Inches(0.1))
        y = Inches(2.05)
        _rect(s, x, y, cw2, Inches(3.05), MGRAY, clr)
        _box(s, x+Inches(0.12), y+Inches(0.1), cw2-Inches(0.24), Inches(0.42),
             title, size=13, bold=True, color=clr)
        _box(s, x+Inches(0.12), y+Inches(0.54), cw2-Inches(0.24), Inches(0.26),
             badge, size=9, italic=True, color=GOLD)
        _rect(s, x+Inches(0.12), y+Inches(0.84), cw2-Inches(0.24), Inches(0.03), clr)
        yy = y + Inches(0.95)
        for item in items:
            _box(s, x+Inches(0.12), yy, cw2-Inches(0.24), Inches(0.38),
                 f"▸  {item}", size=9.5, color=LGRAY, wrap=True)
            yy += Inches(0.40)

    # Full catalog — remaining 10
    _box(s, LM, Inches(5.26), CW, Inches(0.28),
         "Plus 10 more expert workflows:", size=10, bold=True, color=GOLD)

    more = [
        ("ATO Package Builder",         "15 reqs"),
        ("CDRL Generator",              "12 reqs"),
        ("Incident Response Plan",      "10 reqs"),
        ("Program Status Review",       "8 reqs"),
        ("SBOM Attestation",            "10 reqs"),
        ("FedRAMP Auth Prep",           "15 reqs"),
        ("Privacy Impact Assessment",   "10 reqs"),
        ("Section 508 Audit",           "8 reqs"),
        ("Grant Tech Proposal",         "10 reqs"),
        ("CJIS Compliance",             "12 reqs"),
    ]
    tw = (CW - Inches(0.36)) / 5
    for i, (label, reqs) in enumerate(more):
        col, row = i % 5, i // 5
        x = LM + col*(tw+Inches(0.09))
        y = Inches(5.60) + row*Inches(0.62)
        _rect(s, x, y, tw, Inches(0.55), MGRAY, MGRAY)
        _box(s, x+Inches(0.08), y+Inches(0.04), tw-Inches(0.16), Inches(0.28),
             label, size=9, bold=True, color=LGRAY)
        _box(s, x+Inches(0.08), y+Inches(0.32), tw-Inches(0.16), Inches(0.2),
             reqs, size=8, color=GOLD, italic=True)

    _box(s, LM, Inches(6.92), CW, Inches(0.24),
         "Add new use cases by editing args/use_cases.yaml — zero Python required.",
         size=9, color=MGRAY, align=PP_ALIGN.CENTER, italic=True)


def s10_digital_twin(prs):
    s = _blank(prs); _bg(s)
    _title(s, "Digital Program Twin — See the Future Before You Build It",
           "Simulate outcomes across six dimensions before committing a single requirement.")
    _footer(s, 10)

    dims = [
        ("Architecture", "Components, coupling,\nAPI surface, data flow"),
        ("Compliance",   "Control coverage,\nboundary tier impact"),
        ("Supply Chain", "Dependencies, vendors,\nSBOM delta, CVE risk"),
        ("Schedule",     "PERT estimates, critical\npath, PI count"),
        ("Cost",         "T-shirt roll-up, infra\ndelta, vendor licensing"),
        ("Risk",         "Compound score, risk\ninteractions, mitigation"),
    ]
    dw = (CW - Inches(0.25)) / 6
    y = Inches(2.05)
    for i, (dim, desc) in enumerate(dims):
        x = LM + i*(dw+Inches(0.05))
        _rect(s, x, y, dw, Inches(1.55), MGRAY, BLUE)
        _box(s, x+Inches(0.06), y+Inches(0.1), dw-Inches(0.12), Inches(0.42),
             dim, size=11, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
        _box(s, x+Inches(0.06), y+Inches(0.58), dw-Inches(0.12), Inches(0.85),
             desc, size=9, color=LGRAY, align=PP_ALIGN.CENTER)

    _box(s, LM, Inches(3.73), CW, Inches(0.35),
         "10,000 Monte Carlo iterations  →  "
         "P10 (optimistic)  /  P50 (likely)  /  P80 (reserve)  /  P90 (conservative)",
         size=11, bold=True, color=GOLD, align=PP_ALIGN.CENTER)

    coas = [
        ("Speed COA",          "1–2 PIs",  "LOW cost",  "HIGH risk",
         "P1 requirements only.\nFastest path to capability.\nTechnical debt incurred.", LGRAY),
        ("Balanced COA  ★","2–3 PIs",  "MED cost",  "MOD risk",
         "P1 + P2. Best tradeoff.\nRecommended for most programs.\nFull capability delivery.", GREEN),
        ("Comprehensive COA",  "3–5 PIs",  "HIGH cost", "LOW risk",
         "Full scope. All requirements.\nLowest residual risk.\nLongest delivery timeline.", LGRAY),
    ]
    tw = (CW - Inches(0.2)) / 3
    y = Inches(4.22)
    for i, (name, timeline, cost, risk, desc, clr) in enumerate(coas):
        x = LM + i*(tw+Inches(0.1))
        _rect(s, x, y, tw, Inches(2.42), MGRAY, clr)
        _box(s, x+Inches(0.1), y+Inches(0.1), tw-Inches(0.2), Inches(0.4),
             name, size=12, bold=True, color=clr, align=PP_ALIGN.CENTER)
        for j, val in enumerate([timeline, cost, risk]):
            _box(s, x+Inches(0.1), y+Inches(0.56)+j*Inches(0.32),
                 tw-Inches(0.2), Inches(0.28), val, size=10,
                 color=GREEN if "LOW" in val or "★" in name else LGRAY)
        _box(s, x+Inches(0.1), y+Inches(1.57), tw-Inches(0.2), Inches(0.75),
             desc, size=9, color=LGRAY, italic=True, wrap=True)

    _box(s, LM, Inches(6.75), CW, Inches(0.35),
         "No commercial platform offers program-level simulation with this depth. "
         "ICDEV™ is the only one.",
         size=11, italic=True, color=GOLD, align=PP_ALIGN.CENTER)


def s11_academy(prs):
    s = _blank(prs); _bg(s)
    _title(s, "FORGE Academy — 100 Developers in 8 Weeks",
           "Gamified, role-based AI training purpose-built for government software teams.")
    _footer(s, 11)

    stats = [("12", "Role Tracks"), ("75", "Missions"),
             ("5", "Ranks"), ("3", "Certifications"), ("4 × 25", "Cohort Model")]
    sw = CW / len(stats) - Inches(0.1)
    for i, (n, l) in enumerate(stats):
        x = LM + i*(sw+Inches(0.1))
        _rect(s, x, Inches(2.05), sw, Inches(1.05), MGRAY, GOLD)
        _box(s, x, Inches(2.1), sw, Inches(0.55),
             n, size=30, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
        _box(s, x, Inches(2.65), sw, Inches(0.35),
             l, size=10, color=LGRAY, align=PP_ALIGN.CENTER)

    ranks = [
        ("Recruit",   "0 XP",       "Uses AI tools safely"),
        ("Operative", "500 XP",     "Builds basic AI features"),
        ("Specialist","2,000 XP",   "Designs multi-agent systems"),
        ("Architect", "5,000 XP",   "Builds AI platforms"),
        ("Sensei",    "10,000+ XP", "Defines AI strategy"),
    ]
    rw = (CW - Inches(0.6)) / 5
    for i, (rank, xp, cap) in enumerate(ranks):
        x = LM + i*(rw+Inches(0.15))
        clr = [LGRAY, LGRAY, BLUE, GREEN, GOLD][i]
        _rect(s, x, Inches(3.28), rw, Inches(1.05), MGRAY, clr)
        _box(s, x, Inches(3.33), rw, Inches(0.38),
             rank, size=11, bold=True, color=clr, align=PP_ALIGN.CENTER)
        _box(s, x, Inches(3.72), rw, Inches(0.25),
             xp, size=9, color=LGRAY, align=PP_ALIGN.CENTER)
        if i < 4:
            _box(s, x+rw+Inches(0.03), Inches(3.65), Inches(0.11), Inches(0.35),
                 "→", size=13, color=GOLD, align=PP_ALIGN.CENTER)

    cols = [
        ("Role Tracks (12)",
         ["DevOps  ·  DataOps  ·  SecOps",
          "SWE / Architect  ·  NetOps  ·  SRE",
          "ISSO  ·  ISSM  ·  CISO",
          "PM  ·  Analyst  ·  Leadership"]),
        ("Certification Gates",
         ["Foundation (8 wks) — Tier 1 + role Tier 2",
          "Practitioner (+4 wks) — design score ≥80 + GameDay",
          "Expert (Capstone) — top-50% GameDay finish",
          "Auto-currency: Genesis daemon updates curriculum"]),
        ("Developer Outcomes",
         ["Day 1: deploys first LLM feature via ICDEV",
          "Week 4: designs multi-agent system on AADC canvas",
          "Week 8: Foundation-certified, Kanban-ready",
          "Week 12+: mentors next cohort (Sensei path)"]),
    ]
    cw2 = (CW - Inches(0.2)) / 3
    for i, (heading, items) in enumerate(cols):
        x = LM + i*(cw2+Inches(0.1))
        _box(s, x, Inches(4.52), cw2, Inches(0.38),
             heading, size=12, bold=True, color=GOLD)
        for j, item in enumerate(items):
            _box(s, x, Inches(4.95)+j*Inches(0.38),
                 cw2, Inches(0.35), f"▸  {item}", size=10, color=LGRAY)


def s12_gameday(prs):
    s = _blank(prs); _bg(s)
    _title(s, "AI GameDay — Skills Tested Under Fire",
           "Live, AI-scored wargame. Four teams. Five injects. One After-Action Report.")
    _footer(s, 12)

    teams = [
        ("RED\nTHREAT",      RED,   "Attack design\nThreat emulation\nExploit engineering"),
        ("BLUE\nDEFENSE",    BLUE,  "Incident response\nDefense posture\nContainment"),
        ("GOLD\nINNOVATION", GOLD,  "Novel AI modules\nTraining pair generation\nML sprint"),
        ("GREEN\nCOMPLIANCE",GREEN, "NIST verdicts\nEthics assessment\nPolicy guidance"),
    ]
    tw = (CW - Inches(0.3)) / 4
    y = Inches(2.05)
    for i, (name, clr, roles) in enumerate(teams):
        x = LM + i*(tw+Inches(0.1))
        _rect(s, x, y, tw, Inches(1.85), MGRAY, clr)
        _box(s, x+Inches(0.08), y+Inches(0.1), tw-Inches(0.16), Inches(0.55),
             name, size=12, bold=True, color=clr, align=PP_ALIGN.CENTER)
        _box(s, x+Inches(0.08), y+Inches(0.68), tw-Inches(0.16), Inches(0.95),
             roles, size=10, color=LGRAY, align=PP_ALIGN.CENTER)

    _box(s, LM, Inches(4.07), CW, Inches(0.38),
         "Operation CIPHER FORGE — Five Injects (4-Hour Event)", size=13, bold=True, color=GOLD)

    injects = [
        ("Signal\nCluster",     "15 min", "SIGINT threat\nassessment"),
        ("COA\nPosture",        "40 min", "Force posture\nrecommendation"),
        ("Ransomware\nCascade", "Seq",    "Contain, eradicate,\nrecover"),
        ("Fine-Tune\nSprint",   "Seq",    "Generate + evaluate\nML training pairs"),
        ("War Council\nBrief",  "Final",  "Executive synthesis\nof all learnings"),
    ]
    iw = CW / len(injects) - Inches(0.06)
    for i, (name, dur, desc) in enumerate(injects):
        x = LM + i*(iw+Inches(0.06))
        _rect(s, x, Inches(4.55), iw, Inches(1.38), MGRAY, BLUE)
        _box(s, x+Inches(0.08), Inches(4.62), iw-Inches(0.16), Inches(0.42),
             name, size=10, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
        _box(s, x+Inches(0.08), Inches(5.04), iw-Inches(0.16), Inches(0.28),
             dur, size=10, color=GOLD, align=PP_ALIGN.CENTER)
        _box(s, x+Inches(0.08), Inches(5.33), iw-Inches(0.16), Inches(0.52),
             desc, size=9, color=LGRAY, align=PP_ALIGN.CENTER, wrap=True)

    _box(s, LM, Inches(6.08), CW, Inches(0.3),
         "Scoring: Adversarial 40%  ·  Innovation 25%  ·  Compliance 20%  ·  Training Pairs 15%"
         "  ·  AI-judged  ·  Server-verified receipts",
         size=10, color=LGRAY, align=PP_ALIGN.CENTER)
    _box(s, LM, Inches(6.42), CW, Inches(0.35),
         "Output: Auto-generated AAR  ·  Live leaderboard  ·  "
         "Fine-tune pairs feed directly into model improvement  ·  Required for Practitioner cert",
         size=10, color=MGRAY, align=PP_ALIGN.CENTER)


def s13_compounding(prs):
    s = _blank(prs); _bg(s)
    _title(s, "Every Program Makes the Next One Faster",
           "ICDEV™ generates living systems — they inherit the full platform and improve the shared knowledge base.")
    _footer(s, 13)

    _box(s, LM, Inches(2.05), CW, Inches(0.55),
         "Programs built with ICDEV™ are not finished products. They are living systems.",
         size=16, bold=True, color=GOLD, align=PP_ALIGN.CENTER)

    points = [
        ("Generated systems inherit the full platform",
         "Every app ICDEV builds gets its own FORGE framework, agent architecture, "
         "memory system, CI/CD, and canvas suite. They can build their own next features "
         "using the same ANVIL workflow that built them."),
        ("Knowledge compounds across programs",
         "Every build adds to the shared knowledge graph. Patterns discovered in Program A "
         "are automatically surfaced when Program B starts the same task. "
         "Genesis daemon promotes proven patterns into new Academy missions."),
        ("Teams get faster, not slower",
         "Each cohort of 25 developers who complete FORGE Academy become internal Senseis "
         "who train the next cohort. By Year 2, your AI Lab runs itself."),
        ("Compliance artifacts accumulate, not repeat",
         "Control evidence generated in Program A is reused by Program B. "
         "The crosswalk engine means you never map the same control twice. "
         "ATO becomes a natural output — developers don’t even think about it."),
    ]

    cw2 = (CW - Inches(0.1)) / 2
    for i, (heading, body) in enumerate(points):
        col, row = i % 2, i // 2
        x = LM + col*(cw2+Inches(0.1))
        y = Inches(2.8) + row*Inches(1.75)
        _rect(s, x, y, cw2, Inches(1.6), MGRAY, [GOLD, BLUE, GREEN, TEAL][i])
        _box(s, x+Inches(0.12), y+Inches(0.1), cw2-Inches(0.24), Inches(0.42),
             heading, size=12, bold=True, color=[GOLD, BLUE, GREEN, TEAL][i])
        _box(s, x+Inches(0.12), y+Inches(0.55), cw2-Inches(0.24), Inches(0.95),
             body, size=10, color=LGRAY, wrap=True)

    _box(s, LM, Inches(6.45), CW, Inches(0.42),
         "Year 1: stand up the lab.  "
         "Year 2: every program team is self-sufficient.  "
         "Year 3: your organization is the AI center of excellence.",
         size=12, bold=True, color=GOLD, align=PP_ALIGN.CENTER)


def s14_build_vs_build(prs):
    s = _blank(prs); _bg(s)
    _title(s, "Build Alone vs. Build With ICDEV™",
           "The question is not whether to build — it’s whether to build with a platform or without one.")
    _footer(s, 14)

    rows = [
        ("Dimension",            "Building Without ICDEV™",              "Building With ICDEV™"),
        ("Setup time",           "Weeks of scaffolding before first feature", "First feature on Day 1"),
        ("Design",               "Whiteboard → doc → ticket",       "Canvas → simulate → execute"),
        ("AI capability",        "Build LLM wrappers from scratch",           "12 canvases + 15 agents ready to go"),
        ("Simulation",           "Spreadsheet estimates, best guesses",       "Digital Twin: 6 dims, 3 COAs, Monte Carlo"),
        ("Multi-cloud",          "Re-learn each CSP per program",             "6 CSPs, IaC generation, equivalence maps"),
        ("Legacy modernization", "Greenfield rebuild or nothing",             "7Rs assessment + migration canvas + ATO bridge"),
        ("Developer growth",     "Ad hoc, on-the-job, expensive training",   "FORGE Academy: 12 roles, certified in 8 weeks"),
        ("Knowledge transfer",   "Walks out the door when people leave",     "Knowledge graph + Genesis daemon: never lost"),
        ("Compliance artifacts", "Manual, post-hoc, months of ISSO time",    "Generated automatically — a byproduct of building"),
    ]

    col_widths = [Inches(2.4), Inches(4.65), Inches(4.65)]
    y = Inches(2.05)
    for r_idx, row in enumerate(rows):
        x = LM
        is_hdr = r_idx == 0
        for c_idx, (cell, cw2) in enumerate(zip(row, col_widths)):
            bg = {0: MGRAY, 1: NAVY, 2: MGRAY}[c_idx] if not is_hdr else {0: MGRAY, 1: MGRAY, 2: GREEN}[c_idx]
            tc = GOLD if is_hdr else (WHITE if c_idx == 0 else (LGRAY if c_idx == 1 else WHITE))
            _rect(s, x, y, cw2-Inches(0.02), Inches(0.42), bg)
            _box(s, x+Inches(0.1), y+Inches(0.04), cw2-Inches(0.15), Inches(0.35),
                 cell, size=10 if not is_hdr else 11,
                 bold=is_hdr or c_idx == 0, color=tc)
            x += cw2
        y += Inches(0.43)

    _box(s, LM, Inches(6.58), CW, Inches(0.38),
         "Compliance and ATO in the last row — because with ICDEV™, developers never have to think about it.",
         size=11, italic=True, color=GOLD, align=PP_ALIGN.CENTER)


def s15_lab_vision(prs):
    s = _blank(prs); _bg(s)
    _title(s, "The AI Lab — What You’re Standing Up",
           "A self-improving developer platform that trains your people and accelerates every program.")
    _footer(s, 15)

    cx = LM + CW/2 - Inches(2.0)
    _rect(s, cx, Inches(2.4), Inches(4.0), Inches(1.55), MGRAY, GOLD)
    _box(s, cx+Inches(0.15), Inches(2.52), Inches(3.7), Inches(0.5),
         "ICDEV™ Platform", size=18, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
    _box(s, cx+Inches(0.15), Inches(3.04), Inches(3.7), Inches(0.7),
         "12 Canvases  ·  15 Agents  ·  FORGE + ANVIL\n"
         "Digital Twin  ·  Chat + Use Cases",
         size=10, color=LGRAY, align=PP_ALIGN.CENTER)

    sats = [
        (LM,                     Inches(2.2),  "FORGE Academy",
         "12 roles, 75 missions\n4 cohorts × 25 devs, 8 wks\nCertified AI workforce", BLUE),
        (LM,                     Inches(4.3),  "AI GameDay",
         "4 teams, 5 injects\nQuarterly wargames\nAAR + certification", GREEN),
        (W-LM-Inches(3.4),       Inches(2.2),  "Cloud Lab",
         "AWS GovCloud / Azure Gov\nK8s, 15 agents live\nIL4+ environment", GOLD),
        (W-LM-Inches(3.4),       Inches(4.3),  "100+ Developers",
         "AI-trained, certified\nSensei bench by Month 8\nSelf-sustaining by Year 2", TEAL),
    ]
    for sx, sy, title, desc, clr in sats:
        _rect(s, sx, sy, Inches(3.3), Inches(1.55), MGRAY, clr)
        _box(s, sx+Inches(0.1), sy+Inches(0.1), Inches(3.1), Inches(0.42),
             title, size=13, bold=True, color=clr)
        _box(s, sx+Inches(0.1), sy+Inches(0.55), Inches(3.1), Inches(0.85),
             desc, size=10, color=LGRAY)
        arrow = "→" if sx == LM else "←"
        ax = sx+Inches(3.32) if sx == LM else sx-Inches(0.5)
        _box(s, ax, sy+Inches(0.55), Inches(0.45), Inches(0.45),
             arrow, size=18, bold=True, color=GOLD, align=PP_ALIGN.CENTER)

    _box(s, LM, Inches(6.12), CW, Inches(0.6),
         "Every program powered by ICDEV™ trains your team, improves the knowledge base, "
         "and generates its own compliance artifacts — compounding returns across the enterprise.",
         size=11, color=LGRAY, align=PP_ALIGN.CENTER, wrap=True)


def s16_the_ask(prs):
    s = _blank(prs); _bg(s)
    _title(s, "What We’re Asking For",
           "Three approvals. One platform. Returns from Day 90.")
    _footer(s, 16)
    _notes(s,
        "SPEAKER NOTE — COST ESTIMATES (not validated figures)\n\n"
        "All dollar ranges on this slide are market-rate estimates only. "
        "They have NOT been validated against actual vendor quotes, procurement data, "
        "or your organization’s labor category rates. Replace before presenting to a CFO "
        "or contracting officer.\n\n"
        "Infrastructure ($150K–$300K)\n"
        "  Basis: AWS GovCloud or on-prem K8s at roughly $12K–$25K/month annualized "
        "for the specified spec (64 vCPU, 256 GB RAM, 10 TB NVMe) plus LLM API credits. "
        "Replace with an actual AWS GovCloud / Azure Gov quote for this exact configuration.\n\n"
        "Personnel ($300K–$400K)\n"
        "  Basis: 4 FTEs at a blended $75K–$100K/year — assumes a mix of GS-13 equivalents "
        "and contractor labor. Replace with your actual GS salary tables or IDIQ labor "
        "category rates for the four roles listed.\n\n"
        "Academy + GameDay ($120K–$160K)\n"
        "  Basis: ~$20K–$40K per cohort × 4 cohorts, rough facilitation and compute estimate. "
        "Adjust based on whether delivery is in-person or virtual, and whether facilitators "
        "are internal staff or contracted.\n\n"
        "Year-1 Return ($4M–$6M+)\n"
        "  Basis: If one program compresses ATO from 15 months to 2 months, and burns "
        "$500K/month in program labor during that period, the saved 13 months = $6.5M. "
        "Replace with a real program data point from your portfolio if available. "
        "This figure will be the most scrutinized number in the room."
    )

    asks = [
        ("1.  Infrastructure",
         "2-node Kubernetes cluster  ·  64 vCPU, 256 GB RAM, 10 TB NVMe\n"
         "AWS Bedrock (Claude) or Azure OpenAI Gov access (IL4+)\n"
         "GitLab EE + isolated lab VLAN + CAC auth\n"
         "Timeline: 30-day procurement  ·  Budget: $150K–$300K",
         BLUE),
        ("2.  Personnel (4 FTEs)",
         "AI Lab Director — strategy, stakeholder liaison, program integration\n"
         "Platform / MLOps Engineer — ICDEV deployment, agent health, LLM integration\n"
         "Training Lead — Academy curriculum, cohort scheduling, GameDay facilitation\n"
         "ISSO — lab authorization, audit trail review, POA&M management",
         GREEN),
        ("3.  Budget Authority (Year 1)",
         "Infrastructure (cloud + K8s): $150K–$300K\n"
         "Personnel (4 FTEs): $300K–$400K\n"
         "Academy (4 cohorts × 25 devs) + GameDay (4 events): $120K–$160K\n"
         "ICDEV™ platform: $0 (Apache 2.0 open source)\n"
         "Total: $570K–$860K  against a Year-1 return of $4M–$6M+",
         GOLD),
    ]
    y = Inches(2.05)
    for title, body, clr in asks:
        _rect(s, LM, y, CW, Inches(1.5), MGRAY, clr)
        _box(s, LM+Inches(0.15), y+Inches(0.1), CW-Inches(0.3), Inches(0.42),
             title, size=15, bold=True, color=clr)
        _box(s, LM+Inches(0.15), y+Inches(0.56), CW-Inches(0.3), Inches(0.84),
             body, size=11, color=LGRAY, wrap=True)
        y += Inches(1.62)


def s17_why_now(prs):
    s = _blank(prs); _bg(s)
    _title(s, "Why Now",
           "The mandate to build AI-capable government software teams has arrived.")
    _footer(s, 17)

    drivers = [
        ("OMB M-25-21 + M-26-04",
         "Federal agencies must demonstrate responsible, unbiased, transparent AI. "
         "ICDEV™ generates model cards, system cards, fairness assessments, and AI inventory "
         "automatically — as a byproduct of building.",
         GOLD),
        ("DoD AI Workforce Strategy",
         "DoD requires AI literacy across all personnel grades with NICE KSA alignment. "
         "FORGE Academy is already crosswalked to the DoD AI Workforce Framework. "
         "Every mission maps to a KSA.",
         BLUE),
        ("The Competitive Window",
         "Organizations that build AI-native developer platforms now will have a 3–5 year "
         "head start over those that wait. The tools exist. The talent can be trained. "
         "The only question is whether you move first.",
         GREEN),
        ("The Cost of Waiting",
         "Every month without a platform is another month of developers re-inventing scaffolding, "
         "re-learning compliance, and re-training from scratch. "
         "The opportunity cost compounds just as fast as the returns would.",
         TEAL),
    ]

    cw2 = (CW - Inches(0.1)) / 2
    for i, (title, body, clr) in enumerate(drivers):
        col, row = i % 2, i // 2
        x = LM + col*(cw2+Inches(0.1))
        y = Inches(2.05) + row*Inches(2.1)
        _rect(s, x, y, cw2, Inches(1.95), MGRAY, clr)
        _box(s, x+Inches(0.12), y+Inches(0.1), cw2-Inches(0.24), Inches(0.42),
             title, size=13, bold=True, color=clr)
        _box(s, x+Inches(0.12), y+Inches(0.56), cw2-Inches(0.24), Inches(1.22),
             body, size=11, color=LGRAY, wrap=True)

    _box(s, LM, Inches(6.35), CW, Inches(0.42),
         "ICDEV™ is already mapped to every current federal AI mandate. "
         "Your organization doesn’t have to figure it out — we already did.",
         size=13, bold=True, color=GOLD, align=PP_ALIGN.CENTER)


def s18_next_steps(prs):
    s = _blank(prs); _bg(s)
    _rect(s, 0, 0, W, Inches(0.1), GOLD)
    _rect(s, 0, H-Inches(0.1), W, Inches(0.1), GOLD)
    _footer(s, 18)

    _box(s, LM, Inches(0.25), CW, Inches(0.75),
         "Next Steps", size=42, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
    _gold_bar(s, Inches(1.02), Inches(0.04))

    steps = [
        ("Day 1–30",  "Approve AI Lab stand-up",
         "Authorize infrastructure procurement. Designate AI Lab Director and ISSO. "
         "Begin FY procurement action. ICDEV™ deployed and health-checked by Day 30."),
        ("Day 31–60", "Onboard first program team",
         "First team onboarded to canvases. RICOAS intake session run. "
         "Digital Twin simulation executed. First set of compliance artifacts generated "
         "automatically — without anyone asking for them."),
        ("Day 61–90", "Launch Academy Cohort 1 + GameDay #1",
         "25 developers begin 8-week FORGE Academy track. "
         "AI GameDay #1 executed with 4 teams. AAR published. "
         "Lessons learned fed back into Academy curriculum automatically."),
    ]

    y = Inches(1.3)
    for date, title, body in steps:
        _rect(s, LM, y, CW, Inches(1.55), MGRAY, GOLD)
        _box(s, LM+Inches(0.15), y+Inches(0.1), Inches(1.45), Inches(0.42),
             date, size=14, bold=True, color=GOLD)
        _box(s, LM+Inches(1.65), y+Inches(0.1), CW-Inches(1.8), Inches(0.42),
             title, size=14, bold=True, color=WHITE)
        _box(s, LM+Inches(0.15), y+Inches(0.56), CW-Inches(0.3), Inches(0.86),
             body, size=11, color=LGRAY, wrap=True)
        y += Inches(1.68)

    _rect(s, LM, Inches(6.32), CW, Inches(0.52), MGRAY, GOLD)
    _box(s, LM+Inches(0.15), Inches(6.37), CW-Inches(0.3), Inches(0.42),
         "One platform.  Every program.  Starting Day 1.",
         size=20, bold=True, color=GOLD, align=PP_ALIGN.CENTER)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)

    prs = _new_prs()

    s01_cover(prs)
    s02_developer_reality(prs)
    s03_the_vision(prs)
    s04_forge(prs)
    s05_anvil(prs)
    s06_canvases_overview(prs)
    s07_canvas_in_action(prs)
    s08_chat_use_cases(prs)
    s09_use_cases(prs)
    s10_digital_twin(prs)
    s11_academy(prs)
    s12_gameday(prs)
    s13_compounding(prs)
    s14_build_vs_build(prs)
    s15_lab_vision(prs)
    s16_the_ask(prs)
    s17_why_now(prs)
    s18_next_steps(prs)

    out = out_dir / "ICDEV_Executive_Deck.pptx"
    prs.save(str(out))
    print(f"[OK] {out}  ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
