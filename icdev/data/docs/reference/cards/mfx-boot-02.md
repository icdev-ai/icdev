# A crash-looping self-hosted CI runner is re-registered with a fresh token (mfx-boot-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/genesis/daemon.py --reflex ci_runner_health --json     # one cycle through the daemon (ACTS)
python tools/genesis/reflexes/ci_runner_health.py                   # hand-run: DRY RUN, proves and acts on nothing
python tools/genesis/reflexes/ci_runner_health.py --apply           # hand-run, acting
python tools/awareness/capability_consumption.py --class reflex --json
```

MEASURED 2026-09-03 (and twice before): every docker runner -- icdev-ft-runner,
-2, -3, icdev-rt-runner -- sat in `Restarting (1)` for hours with `NotFound
from POST .../actions/runner-registration` (an expired registration token
baked into the container's env), the forge listed each one offline, and every
job in icdev_ft / icdev_rt QUEUED with no red anywhere. Recovered BY HAND each
time: mint a token, `docker compose -p <project> up -d`. The reflex (15 min)
performs exactly that act for exactly the fleet DECLARED in
args/ci_runners.yaml -- repo, compose dir, container / runner / compose-project
names, and NO TOKEN (tested by shape). The token is minted at the moment of
the act and travels in the child's ENVIRONMENT only: never argv, never a row.
TWO SIGNALS, AND ONLY THEIR INTERSECTION IS ACTED ON: the forge's offline set
(`gh api repos/<repo>/actions/runners`) x docker's Restarting set
(`docker ps -a`). A container in one set only is REPORTED and never touched
-- offline-but-Up is a network event a token does not fix, Restarting-but-
online is a race the next poll settles. A candidate is then PROVEN from its
own log: a registration-failure signature (each one taken from a live crash
loop) or it is `restarting_unproven` -- recreating a container that is
crash-looping for another reason destroys the evidence and fixes nothing.
A live compose-project label that disagrees with the declaration REFUSES:
on 2026-08-30 an RT `up` adopted FT's `runner` project and recreated
icdev-ft-runner out from under it.
prove -> audit -> apply -> confirm, in that order: the intent row
(`self_heal_triggered` / ci_runner_health.reregister.intent -- an EXISTING
type, so no CHECK rebuild) is written with raise_on_error BEFORE the act, and
no row means no act. apply is ONE `docker compose up -d`; the module never
runs `docker rm`, `compose down` or drops a volume, by test. confirm re-reads
docker AND the forge: `applied` means the forge lists the runner online;
`applied_unconfirmed` is never `applied`. Bounded per run (deferred
candidates are still proven and NAMED) and cooled per container from its own
intent rows -- a runner still looping after a fresh token has a different
defect a second token cannot repair. UNMEASURABLE, never ok, when the
declaration, docker or the forge cannot answer; `success` stays True so a
host with no docker does not trip the breaker and make the reflex inert.
Registered in BOTH daemon.REFLEX_NAMES and args/genesis_config.yaml, and
dispatched once through the daemon on the day it landed -- a reflex that has
never run fails capability_liveness at budget 0, on purpose.
