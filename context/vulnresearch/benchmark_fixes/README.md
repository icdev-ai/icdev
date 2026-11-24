# Reference fixes for the synthetic benchmark (icx-ent-01)

The committed fixes for two planted weaknesses (`vb_cmdi`, `vb_parser`), as unified diffs against
`../benchmark`. Under the benchmark's isolation waiver (a plain container, no gVisor or microVM), a patched copy
runs only when every changed file's SHA-256 is one of `benchmark.reference_fixes` in `args/vuln_research.yaml`,
which these diffs produce (`python tools/vulnresearch/cli.py benchmark-digest --check` recomputes them). Any other
patch is code that is not ours and needs gVisor or a microVM.
