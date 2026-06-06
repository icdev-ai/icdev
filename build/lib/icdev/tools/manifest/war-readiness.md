# CUI // SP-CTI
# Manifest: War Readiness Intelligence

Tools for computing composite war-readiness scores — information warfare
pressure, cyber-reconnaissance, and disinformation surge signals — stored in
the `sg_information_signals` and `sg_information_scores` tables.

---

## tools/intelligence/war_readiness/information_scorer.py

Information Signal Scorer — NLP-based information warfare pressure index
(rhetoric, dehumanization, cyber-recon, disinformation surge) → composite
0–10 score persisted to `sg_information_scores`.

**CLI flags**

| Flag | Description |
|------|-------------|
| `--scenario-id UUID` | Load signals from DB for the given scenario UUID |
| `--params-file FILE` | JSON file with full params dict |
| `--demo` | Run hostile-invasion demo scenario |
| `--demo-peaceful` | Run peaceful/NATO demo scenario |
| `--no-persist` | Skip DB write |
| `--json` | JSON output (for scripting / kanban wiring) |

**Key functions**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `score_rhetoric` | `(news_items: Sequence[dict]) → dict` | Keyword + sentiment + Goldstein → rhetoric score 0–10 |
| `score_dehumanization` | `(news_items: Sequence[dict]) → dict` | Animal/disease/genocide lexicon → dehumanization index 0–10 |
| `score_cyber_recon` | `(params: dict) → dict` | CUSUM on SCADA probe rates → cyber recon score 0–10 |
| `score_disinformation_surge` | `(news_items, *, window_size, baseline_mean, baseline_std) → dict` | Topic-velocity z-score on IW trigram clusters → surge score 0–10 |
| `compute_information_score` | `(params: dict) → InformationScoreResult` | Composite weighted score (0.30×rhetoric + 0.30×dehum + 0.20×cyber + 0.20×disinfo) |

**Usage examples**

```bash
# Demo hostile-invasion scenario
python tools/intelligence/war_readiness/information_scorer.py --demo --json

# Score a specific scenario from DB
python tools/intelligence/war_readiness/information_scorer.py \
    --scenario-id <uuid> --json

# Score from a params JSON file (no DB write)
python tools/intelligence/war_readiness/information_scorer.py \
    --params-file signals.json --no-persist --json
```

**Output shape**

```json
{
  "scenario_id": "...",
  "information_score": 7.42,
  "rhetoric_score": 8.1,
  "dehumanization_index": 7.6,
  "cyber_recon_score": 5.9,
  "disinformation_surge": 7.0,
  "persisted": true
}
```

**DB tables** — `sg_information_signals`, `sg_information_scores` (migration 055).
Falls back gracefully when DB is unavailable.

**LLM dependency** — Uses HuggingFace `distilbert-sst-2` + `dslim/bert-base-NER`
when `transformers` is installed; degrades to keyword/heuristic mode in
air-gap deployments with no cloud LLM.
