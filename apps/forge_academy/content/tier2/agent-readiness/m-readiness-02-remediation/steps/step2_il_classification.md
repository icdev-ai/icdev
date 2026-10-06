---
ontology_id: icdev:mission:m-readiness-02-remediation:step:2
step_class: icdev:configure
---
# IL Classification Remediation

Pillar 8 (IL Classification) samples the repository's Python files and checks that enough of them carry a classification header in their first lines (`cui_header_ratio: 0.5` in `args/agent_readiness.yaml`). It also checks that `CLAUDE.md` states the IL / classification context, that an IL environment variable is configured, and that `classification_manager` is used instead of hard-coded banners.

## Marking by impact level

ICDEV maps impact levels to classifications in `tools/compliance/classification_manager.py`:

- **IL2** -> PUBLIC
- **IL4 and IL5** -> CUI. The Python code header starts `# CUI // SP-CTI`
- **IL6** -> SECRET. The header starts `# SECRET // NOFORN`

Files that need markings: anything that processes PII, security controls, authentication, keys, or controlled data.

## Use the manager, not a string literal

```python
from tools.compliance.classification_manager import get_classification_for_il, get_code_header

classification = get_classification_for_il("IL4")      # -> "CUI"
header = get_code_header(classification, "python")     # -> multi-line block, first line "# CUI // SP-CTI"
```

`get_code_header` returns the full multi-line header block (controlled-by, category, distribution, POC lines) in the comment style of the language you pass.

## Your task

Plan a script that finds Python files missing a classification header in a target directory and prepends the IL4 header. Write down: how you detect "already marked" (so the script is idempotent), where the header goes in a file that starts with a shebang or `from __future__ import`, and the two `classification_manager` calls you use. Press **Configure** to record that you completed the plan.
