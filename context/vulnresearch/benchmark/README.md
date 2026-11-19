# Vulnerability discovery benchmark (SYNTHETIC, icx-ent-01)

Planted, labelled code for measuring false-positive and false-negative rates of
`tools/vulnresearch` (`python tools/vulnresearch/cli.py measure`). Seven modules carry one planted
weakness each; five are safe look-alikes. Ground truth: `labels.yaml` (labelled by construction,
awaiting a person's review). It is never deployed or imported by ICDEV, and only ever executed inside
the isolated sandbox. The proof inputs are canary-only (an empty file under /tmp).
