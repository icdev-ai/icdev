# CUI // SP-CTI
# Goal: Genesis Goal Learner — Self-Improving Goals from Experience

**Version:** 1.0
**Classification:** CUI // SP-CTI
**Decisions:** D-GEN-GL-1

---

## Purpose

Close the learning loop by auto-generating FORGE goal files whenever an agent
solves a novel problem domain not addressed by any existing goal.  Generated
goals carry a quality score and full version history, and are staged for human
review before activation.

---

## Trigger

- Genesis Daemon fires the `goal_learner` reflex daily at 20:00
- Manual: `python tools/genesis/goal_learner.py --scan --json`

---

## Inputs

- `genesis_audit` table — recent problem-solving event records
- `kanban_tasks` table — recently completed tasks
- `goals/manifest.md` — existing goal coverage index (keyword vocabulary)
- `args/genesis_config.yaml` → `reflexes.goal_learner` block

---

## Novelty Detection Algorithm

1. Tokenize all goal descriptions in `goals/manifest.md` into a keyword vocabulary
2. Collect `genesis_audit` events and `kanban_tasks` from the look-back window
3. Tokenize each event/task and cluster by dominant keyword (domain)
4. For each candidate domain, compute **novelty score** =
   fraction of domain keywords NOT present in the goal vocabulary
5. Domains with `novelty_score ≥ novelty_threshold` (default 0.6) become
   candidates for goal generation

---

## Quality Scoring

| Dimension | Weight | Notes |
|-----------|--------|-------|
| Novelty score | 40% | How much of the domain is uncovered |
| Evidence count | 25% | Number of supporting audit/kanban records |
| Domain clarity | 20% | Avg keyword length — longer = more specific |
| Tool coverage | 15% | Placeholder (future: reference actual tools) |
| **Overall** | **100%** | Stored in `genesis_generated_goals.quality_score` |

---

## Process

### Step 1 — Scan for Novel Domains
```bash
python tools/genesis/goal_learner.py --scan --json
```

### Step 2 — Write Suggested Goal Files (optional)
```bash
python tools/genesis/goal_learner.py --scan --write --json
# Files written to: data/genesis/suggested_goals/
```

### Step 3 — List Pending Goals
```bash
python tools/genesis/goal_learner.py --list --json
python tools/genesis/goal_learner.py --list --status-filter suggested --json
```

### Step 4 — Review Full Markdown
```bash
python tools/genesis/goal_learner.py --review <goal_id>
```

### Step 5 — Approve or Reject
```bash
# Approve: copies file to goals/, appends to manifest.md, exports GKP
python tools/genesis/goal_learner.py --approve <goal_id> --json

# Reject with reason
python tools/genesis/goal_learner.py --reject <goal_id> --reason "Not relevant" --json
```

### Step 6 — Statistics
```bash
python tools/genesis/goal_learner.py --stats --json
```

---

## Outputs

| Artifact | Location | Notes |
|----------|----------|-------|
| Suggested goal markdown | `data/genesis/suggested_goals/` | Human review required |
| Approved goal file | `goals/<slug>.md` | Written on approval only |
| Manifest entry | `goals/manifest.md` | Appended on approval |
| DB record | `genesis_generated_goals` table | Version history + quality score |
| GKP | `genesis_gkp` (`proven_pattern`) | Exported on approval via Promoter |
| Audit | `genesis_audit` | Append-only (NIST AU-2) |

---

## Database Schema

```sql
genesis_generated_goals (
    id              TEXT PRIMARY KEY,         -- gl-xxxxxxxxxx
    version         INTEGER DEFAULT 1,        -- Incremented on re-generation
    domain_label    TEXT NOT NULL,            -- Dominant domain keyword
    title           TEXT NOT NULL,            -- Human-readable title
    slug            TEXT NOT NULL,            -- File slug (goals/<slug>.md)
    novelty_score   REAL,                     -- 0–1, fraction not in goal vocab
    quality_score   REAL,                     -- Weighted composite 0–1
    evidence_count  INTEGER,                  -- Supporting audit + kanban records
    keywords        TEXT,                     -- JSON array of domain keywords
    goal_markdown   TEXT NOT NULL,            -- Full FORGE goal markdown
    sha256          TEXT NOT NULL,            -- Content hash for dedup
    status          TEXT DEFAULT 'suggested', -- suggested|approved|rejected|superseded
    gkp_id          TEXT,                     -- Linked GKP (on approval)
    goal_file_path  TEXT,                     -- Relative path in goals/
    rejection_reason TEXT,
    approved_at     TEXT,
    rejected_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
```

---

## Guardrails

- Goal Learner **never** writes directly to `goals/` — only `data/genesis/suggested_goals/`
- Approval is always a **human action** (`--approve <goal_id>`)
- Maximum `max_goals_per_run` (default 3) goals per reflex invocation
- Deduplication: if a domain was already generated and is still `suggested` or `approved`,
  it is skipped (no duplicate generation)
- All decisions logged to append-only `genesis_audit` (NIST AU-2)
- Quality score below 0.4 is a warning to reject unless evidence is compelling

---

## Related Goals

- `goals/genesis_promoter.md` — GKP gateway (approval exports `proven_pattern` GKP)
- `goals/genesis_daemon.md` — Daemon lifecycle (fires this reflex daily)
- `goals/code_intelligence.md` — Code quality patterns (feeds audit evidence)
