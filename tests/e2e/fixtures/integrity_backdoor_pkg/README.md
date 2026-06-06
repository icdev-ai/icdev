# jsonpretty

A tiny utility that pretty-prints and validates JSON files. Pure formatting,
no network, no subprocess — just reads a file and reformats it.

<!--
SIPA E2E TEST FIXTURE. The "pure formatting, no network, no subprocess" claim
above is the *declared purpose* the intent reconciler compares against the
statically-extracted capability manifest. The accompanying formatter.py hides
network_egress + dynamic_code + obfuscation, so the disclosed-vs-exercised gap
forces a QUARANTINE verdict. This artifact is never executed.
-->
