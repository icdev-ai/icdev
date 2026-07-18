---
ontology_id: icdev:mission:m-t3-05-canvas-selection:step:1
step_class: icdev:Lesson
---

# Canvas Selection

ICDEV is a **registry-driven** platform. Every canvas, child app, feature, and core
extension is declared in `args/component_registry.yaml` and loaded at runtime by
`tools/config/component_registry.py`. There is **no fixed "7 canvas" model** — the
platform ships **30+ canvases** today and grows by editing the registry, not by
editing `app.py`. Before building a child app you pick the canvas whose *problem
domain* matches your app. Choosing the wrong canvas is the single most expensive
architectural mistake you can make at Tier 3.

## The registry is the source of truth

Never memorize a canvas list — read it. Each entry declares a `key` (technical id),
a `display_name`, and a `description`:

```yaml
- key: ndc
  display_name: Network Design Canvas
  description: Topology, routing, capacity, redundancy, EOL analysis.
```

To see what is enabled in an environment:

```bash
icdev list          # every canvas / toggle the platform supports
icdev status        # what is currently enabled in this .env
```

**Core profiles** bundle a coherent set of canvases for a mission. Instead of
enabling canvases one at a time you apply a profile (defined in
`args/core_profiles.yaml`):

```bash
icdev profile list          # available profiles
icdev profile show          # the active profile
icdev profile apply <name>  # enable that profile's canvases in .env
```

## Design Canvases — a representative subset

The **Design Canvas** family covers the core engineering lifecycle. These seven are
the ones you will select between most often. Purposes below come **verbatim from the
registry** — do not invent your own:

| Canvas | Code | Purpose (from registry) |
|--------|------|-------------------------|
| **Network Design Canvas** | NDC | Topology, routing, capacity, redundancy, EOL analysis |
| **Security Design Canvas** | SDC | Threat model, hardening, STIGs, attack-path analysis |
| **Pipeline Design Canvas** | PDC | CI/CD pipeline management, worktree isolation, GitLab integration |
| **Boundary & Supply Chain Canvas** | BDC | ATO boundary impact, supply chain SCRM, ISA lifecycle |
| **Data Design Canvas** | DDC | Data lineage, schemas, synthetic data, quality |
| **Observability Design Canvas** | ODC | Logging, monitoring, distributed tracing, SRE |
| **Infrastructure Design Canvas** | IDC | Cloud, IaC, Terraform, K8s manifest management |

Beyond these, the registry also carries the Quality Design Canvas (QDC), Migration
Canvas (MDC), Agentic AI Canvas (AADC), AI/ML Canvas (AIMC), Document Intelligence
Canvas (DIC), Cortex, and many more. When your domain does not match one of the seven
above, **go back to the registry** — do not force a fit.

## Canvas Selection Criteria

Each canvas answers one primary question about your app's *problem domain*:

- **NDC**: "Is this about network topology, routing, capacity, or redundancy?"
- **SDC**: "Is this about threat modeling, hardening, STIGs, or attack paths?"
- **PDC**: "Is this about CI/CD pipelines, build/deploy stages, or worktree isolation?"
- **BDC**: "Is this about the ATO boundary, supply chain / SCRM, or ISAs?"
- **DDC**: "Is this about data schemas, lineage, synthetic data, or data quality?"
- **ODC**: "Is this about logging, metrics, distributed tracing, or SLOs?"
- **IDC**: "Is this about cloud infrastructure, IaC, Terraform, or Kubernetes?"

## What You'll Build

A `CanvasSelector` that recommends the right canvas given an app description, scoring
each canvas by how many of its domain keywords the description mentions:

```python
selector = CanvasSelector()
result = selector.select("Design redundant WAN routing and size link capacity")
# → {"canvas": "NDC", "name": "Network Design Canvas", "confidence": 0.4, "reasoning": "..."}
```

## Selection Logic

Each canvas has domain keyword signals. Score each canvas by keyword matches, and
return the highest scorer. If **nothing** matches, the selector must **not guess** —
it returns a `"NONE"` sentinel telling you to consult the registry directly.

## Success Criteria

- `score_canvas()` returns a float score for a given canvas code and description
- `explain_canvas()` returns the human-readable purpose for a canvas code
- `select()` returns the top-scoring canvas with confidence and reasoning
- Tie-breaking: when scores are equal, prefer the canvas defined first (registry order)
- Descriptions with **no** matches return `canvas="NONE"` (consult the registry — no guess)
- `rank_canvases()` returns all seven canvases ranked by score, highest first
