# CUI // SP-CTI

# Why the runner's `git worktree add` took 44.5 s and a hand add took 5 s

**Date:** 2026-09-12
**Card:** `mfx-own-08`
**Population:** 91 recorded `git worktree add`s (2026-09-06 → 2026-09-12), 15 killed
(16.48%); 44 fresh interleaved adds run for this survey
**Changed by this card:** nothing. `WORKTREE_ADD_TIMEOUT_SECONDS` is still 30, no
retry was added, no threshold, budget, guard or config value was touched.

---

## Verdict

**The gap is not a property of the caller. It is a property of the MINUTE.**

Not one caller-side candidate reproduces it. Under 44 interleaved adds the full
dispatcher replica — long-lived service parent, BelowNormal priority, the
scheduler's exact environment — ran at **1.10×** the hand add, not 9×.

The host itself is 3–20× slower for minutes at a time, and **those windows are
self-hosted `icdev_ft` / `icdev_rt` CI jobs executing on this same machine**:

| | n | median add | max | killed |
|---|---:|---:|---:|---:|
| a self-hosted CI run in flight | 37 | **23.0 s** | 44.5 s | **15 (40.5%)** |
| none in flight | 54 | **8.1 s** | 29.6 s | **0 (0.0%)** |

Fisher exact, two-tailed, killed × CI-in-flight: **p = 1.7 × 10⁻⁷**.
**All 15 killed adds in the history sat under a CI run. None of the 54 adds with
no CI run in flight was killed.**

For the incident itself the runner container's own log closes it:
`icdev-ft-runner` was `Running job: Test` from **03:14:39Z to 03:33:52Z**, and
both kills — 03:28:46Z (44.5 s) and 03:30:29Z (40.9 s) — fall strictly inside it.
The hand add ~8 minutes later fell **after that job finished**. Nothing about the
caller changed between the two adds; the machine stopped compiling somebody
else's repository.

---

## 1. What the card had already ruled out, and what it had not

Ruled out before this card, not re-derived here: **dispatcher collision**
(mfx-own-06's cross-process lock, live, zero recorded lock waits), **process
priority** (A/B at two disk loads, no difference), **disk load alone** (a hand
add at disk queue 13.2 took 5 s), and **timing the add better** (mfx-own-06's
survey: 0% precision, `docs/audits/mfx-own-06-host-load-gate-survey.md`).

Four candidates were named as untested: Windows Defender, the parent
process/environment, contention on `.git` itself, and memory pressure at spawn.
All four are measured below, plus three the card did not list (the destination
path, Windows-side I/O load, WSL/docker-side I/O load).

---

## 2. A — Prospective: does any caller-side factor reproduce the gap?

**Method.** Every arm runs the byte-identical argv the dispatcher runs —
`git -c checkout.workers=0 worktree add -b <branch> <path> origin/main`, cwd
`C:/AI/ICDev` — and changes exactly one thing. Arms are **interleaved and the
order is rotated every round**, because host load drifts (it did: the round
medians rose 3.73 → 4.06 → 4.06 → 4.57 s across the session) and because the
first checkout of a process pays a cold cache. Every add is taken **under the
shared mfx-own-06 add lock**, and every load arm starts and stops its load
strictly inside that lock, so this survey can never itself be the cause of a
dispatcher's park. Each worktree is removed before the next trial.

### Round 1 — 8 arms × 4 rounds = 32 adds, all rc=0

| arm | what it changes | n | median | range | ×base | Defender CPU |
|---|---|---:|---:|---|---:|---:|
| `interactive` | nothing — short-lived parent, my shell env, **Normal** priority (the hand add) | 4 | **3.56 s** | 3.40–4.29 | 1.00 | 9.0 s |
| `sched_env` | the **scheduler's exact environ** (read from pid 23704 with `psutil`) | 4 | 3.85 s | 3.37–4.24 | **1.08** | 9.4 s |
| `service_child` | **the full replica**: long-lived service parent, BelowNormal, scheduler environ | 4 | 3.93 s | 3.73–4.54 | **1.10** | 9.7 s |
| `bigrss` | parent holding **1.1 GB of touched RSS** at spawn | 4 | 4.04 s | 3.57–4.24 | 1.14 | 10.1 s |
| `gitbusy` | a continuous loop of `git status` / `rev-parse` / `log` / `worktree list` on `.git` | 4 | 4.12 s | 3.74–5.30 | 1.16 | 11.1 s |
| `temp_dest` | destination under `%TEMP%` instead of the repo's `.tmp/worktrees` | 4 | 3.95 s | 3.86–4.59 | 1.11 | 12.4 s |
| `io_load_2` | 2 Windows processes churning 4 KiB files | 4 | 4.32 s | 4.18–4.78 | 1.21 | 16.2 s |
| `io_load_6` | 6 of them | 4 | 5.21 s | 4.53–5.99 | **1.47** | 23.6 s |

### Round 2 — the load that matters, 3 arms × 4 rounds = 12 adds, all rc=0

The runners write to the **WSL vhdx**, not to the Windows filesystem, so the
Windows-side churn above is not the same load. This arm puts three containers on
the vhdx (sustained `dd … conv=fsync` plus small-file churn).

| arm | n | median | range | ×base |
|---|---:|---:|---|---:|
| `interactive` | 4 | 4.27 s | 4.22–**7.03** | 1.00 |
| `win_load` (6 Windows churners) | 4 | 5.66 s | 5.28–5.97 | 1.33 |
| `wsl_load` (3 containers on the vhdx) | 4 | 4.85 s | 4.53–5.33 | 1.14 |

The lone 7.03 s `interactive` trial is reported rather than dropped: it wrote
358 MB against ~200 MB for its siblings and burned 27 s of Defender CPU, so
something unrelated was running. It is the largest single number in 44 trials
and it is still a sixth of 44.5 s.

**Reading.** Every caller-side candidate lands between 1.00× and 1.16×. The only
factor that moves the add at all is concurrent I/O, and even six Windows
churners buy 1.47×. **Nothing I can synthesise reproduces 9×** — stated as a
limitation in §5, not hidden.

---

## 3. B — The host, measured with the caller held constant

The prospective arms say the caller is innocent. To say what *is* guilty I need
a measurement of the host at the minute of each historical add — and the
dispatcher has been taking one all along without anybody reading it.

`_sweep_old_worktrees` walks the same ~50 registered worktrees every cycle and
logs one `Sweep: keeping <path>` line per worktree. **The gap between two
consecutive lines of one pass is a `git status` latency: same caller, same
command, same directory, over and over.** So a pass measures THE HOST with the
two variables this card is about — who the caller is and what it is doing —
held fixed by construction.

Each path is normalised against its own median across all passes (a 20k-file
worktree and an unreadable one are not comparable in absolute seconds); a pass's
**host factor** is the median of those ratios. 1.0 is this host's normal speed.

Over 687 passes: p10 **0.86**, median **0.98**, p90 **2.31**, max **20.33**.

Taking, for each add, only a pass that **ended before the add started** (so the
add cannot be measuring a host it is itself loading), 56 of the 91 adds have a
prior host measurement within 15 minutes:

| | n | prior host factor (median) | range |
|---|---:|---:|---|
| add ≥ 20 s | 15 | **3.06** | 0.97–11.30 |
| add < 10 s | 23 | **1.19** | 0.92–12.13 |

Spearman(add duration, prior host factor) = **0.509**, n = 56.

The same slowdown is legible by eye in the raw log. The sweep pass beginning
03:33:44Z spent **4.0 s** on `git status` in `mfx-own-02`; the pass beginning
03:37:52Z spent **0.33 s** on the same directory. Same process, same command,
four minutes apart, 12× apart.

---

## 4. C — Attribution: self-hosted CI on this same host

ICDEV's own CI cannot be the cause, and that is measured rather than assumed:
`.github/workflows/icdev-ci.yml` is `runs-on: ubuntu-latest` ×12 and
`windows-latest` ×1 — no self-hosted line in it — and
`gh api repos/icdev-ai/icdev/actions/runners` returns `total_count: 0`, so this
repository has **no self-hosted runner registered at all**. (One workflow,
`icdev-kanban-runner.yml`, does declare `self-hosted`; its most recent run is
2026-05-26, four months outside this window, and it has nowhere to run.)
But **four self-hosted runner containers for `icdev_ft` and `icdev_rt` run on
this machine** (`icdev-ft-runner`, `-2`, `-3`, `icdev-rt-runner`), and their
jobs are local CPU and local disk.

### The forge's view — 379 runs, all 91 adds covered

| | n | median add | max | killed | ≥ 20 s |
|---|---:|---:|---:|---:|---:|
| a self-hosted run in flight | 37 | 23.0 s | 44.5 s | **15 (40.5%)** | 20 (54.1%) |
| none in flight | 54 | 8.1 s | 29.6 s | **0 (0.0%)** | 5 (9.3%) |

* killed × CI-in-flight `[[15,22],[0,54]]` — Fisher 2-tailed **p = 1.708e-07**
* ≥ 20 s × CI-in-flight `[[20,17],[5,49]]` — Fisher 2-tailed **p = 3.516e-06**

`createdAt → updatedAt` includes **queue** time, so "in flight" is an upper
bound on the busy period. That dilutes the effect rather than manufacturing it —
the association survives the conservative reading.

### The machine's own view — the runner containers' job logs

Better evidence than the forge window: each runner writes `Running job: X` and
`Job X completed` with timestamps, which is this host saying what it was
executing. The logs reach back to the containers' start (2026-09-11 19:46Z for
all four), covering 24 of the 91 adds.

| | n | median add | killed |
|---|---:|---:|---:|
| a job executing here | 18 | **30.4 s** | 9 |
| no job executing here | 6 | **6.7 s** | 0 |

Fisher 2-tailed p = 5.2e-02 on that smaller table — quoted as it is, not as the
headline; the 91-add table above is the one with the power.

### The incident, minute by minute

```
icdev_ft CI    02:44:41Z -> 03:07:29Z   ] three runs overlapping
icdev_ft CI    02:47:34Z -> 03:10:26Z   ]
icdev_ft CI    02:48:03Z -> 03:13:32Z   ]
  sweep pass   03:02:55Z -> 03:09:19Z   host factor 20.36   <- the host at its worst
  add          03:07:57Z  13.5 s ok     mfx-own-07
icdev_ft CI    03:14:38Z -> 03:33:53Z   (icdev-ft-runner: "Running job: Test"
                                          03:14:39Z -> 03:33:52Z, LOCAL log)
  sweep pass   03:20:15Z -> 03:21:32Z   host factor  2.94
  add          03:20:29Z  11.9 s ok     xrv-route-02
  add          03:28:46Z  44.5 s KILLED xrv-shield-02     <- inside the job
  add          03:30:29Z  40.9 s KILLED xrv-shield-02     <- inside the job
  sweep pass   03:33:44Z -> 03:35:46Z   host factor  5.00  (starts inside, ends after)
  [the operator's hand add, ~03:36Z, 5 s]                  <- after the job ended
  sweep pass   03:37:52Z -> 03:38:07Z   host factor  1.02  <- host back to normal
  add          04:02:20Z   7.4 s ok     mfx-own-08        <- this card's own worktree,
                                                             by the RUNNER, no CI in flight
```

The last line is worth its own sentence. **This card's worktree was created by
the same dispatcher, on the same host, by the same code path, in 7.4 s** — 34
minutes after the 44.5 s kill. The runner is not systematically slow.

---

## 5. What this does NOT establish

* **It is observational, not interventional.** I could not run the experiment
  that would close it — triggering `icdev_ft` CI on demand is an outward-facing
  act on a live pipeline, and the loads I *could* synthesise safely were far too
  light (1.14–1.47×). So the attribution rests on the association (p = 1.7e-07),
  the local execution logs, and a plausible direct mechanism (a full pytest and
  Playwright suite writing to the WSL vhdx on the same physical disk). It is not
  a proven intervention.
* **A residual confound, named:** a self-hosted job executing is correlated with
  `icdev_ft` development activity in general. Against that: ICDEV's own
  concurrent worker sessions show **no** relationship with add duration
  (Spearman −0.015 over all 91 adds, below), so "the host is busy with ICDEV
  work" is not what is being measured.
* **CI in flight is necessary but not sufficient in this sample.** 21 of the 37
  adds under a CI run were fine, and 5 of the 54 with no CI run still took ≥ 20 s
  (max 29.6 s) — but none of those 5 was killed.
* **Defender exclusions could not be read** (`Get-MpPreference` → "Must be an
  administrator to view exclusions"). What *was* measured is per-add MsMpEng CPU
  (below), which is the better question anyway.
* **The runner-log horizon is 2026-09-11 19:46Z.** Adds before that are
  `unmeasurable` on the local-evidence table, never "no job was running".

---

## 6. Candidate-by-candidate disposition

| candidate | how it was measured | verdict |
|---|---|---|
| **1. Windows Defender** | MsMpEng CPU-time delta across **every** add, all 44 trials | **Not the gap.** Real-time protection is on and Defender is expensive — 9.0 s of CPU on a *baseline* 3.56 s add, rising to 23.6 s under load — but it costs the dispatcher replica (9.7 s) and the interactive hand add (9.0 s) the **same**, so process-tree-dependent scanning is unsupported. The one scan start Task Scheduler records (2026-09-11 23:14:43Z) is *after* the whole 20:14–22:36Z kill cluster and four hours before the 03:28Z kills. |
| **2. Parent process / environment** | scheduler environ read from pid 23704 with `psutil`, replayed exactly; long-lived BelowNormal service parent | **Not the gap.** 1.08× for the environ alone, 1.10× for the full replica. The environs differ only in Git-Bash-added keys and `PATH` ordering, and `git config --show-origin` resolves **identically** under both (`core.fscache=true` from the system config either way). |
| **3. Contention on `.git` itself** | a continuous git-command loop against the repo during the add | **Not the gap.** 1.16×. |
| **4. Memory pressure at spawn** | a parent holding 1.1 GB of *touched* RSS | **Not the gap.** 1.14×. Measured at 60–67% host memory, not the 86.8% of the incident — stated, not glossed. |
| destination path (not on the card's list) | `%TEMP%` vs the repo's `.tmp/worktrees` | **Not the gap.** 1.11× — though it costs *more* Defender CPU (12.4 s vs 9.0 s) for the same wall time. |
| concurrent ICDEV worker sessions (not on the card's list) | `in_progress` intervals replayed from `kanban_status_transitions` | **Refuted.** Spearman(duration, workers) = **−0.015** over all 91 adds; kill rate 25% at 0 workers, 13% at 1, 17% at 2. The dispatcher's own dispatches are not the cause. |
| Windows-side I/O load | 2 and 6 small-file churners | contributes: 1.21× / 1.47× |
| WSL/vhdx-side I/O load | 3 containers | contributes: 1.14× (too light to reproduce a CI job) |
| **self-hosted `icdev_ft` / `icdev_rt` CI executing here** | forge windows over 91 adds + the runner containers' own job logs | **THIS ONE.** 15/15 kills under a run, 0/54 without, p = 1.7e-07 |

A note on the worker-session count, because the first version of it was wrong
and the way it was wrong is the point: counting `to_status='in_progress'` and
subtracting `from_status='in_progress'` produced "469 → 487 concurrent workers",
a number that only ever rises. A task can leave `in_progress` through a
transition the log records from another state, so the naive counter is a
cumulative total wearing a concurrency label. An **interval per task** is the
only reading the log actually supports.

---

## 7. What follows from this — and what deliberately does not

**No change is proposed by this card and none was made.** In particular:

* **Do not raise the budget and do not add a retry.** Forbidden by
  `kph-repark-kph-repark-mfx-ci-04`, and the measurement does not argue for it:
  the add is not slow, the host is.
* **Do not build a "CI in flight → decline to dispatch" gate on this.** It is
  the same shape mfx-own-06 already surveyed and refused, and it would fire on
  37 of 91 adds (40.7%) to prevent 15 — 25 times the 1.63% of routine work this
  repo already calls grounds for standing a check down. Any such rung owes its
  own survey, and this one does not support it.
* The two levers the evidence *does* point at are both somebody else's card:
  making the add cheaper (**mfx-own-07**, the `icdev/` mirror question — 1.10×
  of nothing is still nothing if the checkout is 30% smaller), and not putting
  a 19-minute pytest suite and a 20k-file checkout on the same spindle at the
  same time, which is a deployment decision about the self-hosted runners and
  the operator's to make.

The one thing this card leaves behind for free: **the dispatcher's sweep log is
a continuous, caller-constant, already-recorded probe of this host's I/O speed.**
Nothing consumes it. If a later card wants a host-speed signal, that is where it
already exists — and §3's normalisation is how to read it.

---

## 8. Re-deriving every number

The harness is inlined **in full** in the appendix; it lived under a disposable
`.tmp/` scratch directory, which must never be cited as the way to re-derive a
published number. Save the eight blocks into one directory and run:

```bash
# helper.py and loader.py are spawned BY drive2.py / drive3.py -- not run directly
python drive2.py <scheduler-pid> 4    # A, round 1: 8 caller-side arms x 4 rounds
python drive3.py 4                    # A, round 2: interactive / win_load / wsl_load
python sweeplat.py                    # B: host factor from the sweep log
python correlate.py                   # B: host factor vs add duration, + worker sessions
python ci_overlap.py                  # C: forge windows (needs runs_icdev_{ft,rt}.json)
python runner_jobs.py                 # C: the runner containers' own job logs
python final_numbers.py               # every figure quoted above, from one run
```

`ci_overlap.py` reads `runs_icdev_ft.json` / `runs_icdev_rt.json`, written by:

```bash
gh run list -R icdev-ai/icdev_ft --limit 200 \
  --json databaseId,name,status,conclusion,createdAt,updatedAt,headBranch > runs_icdev_ft.json
gh run list -R icdev-ai/icdev_rt --limit 200 \
  --json databaseId,name,status,conclusion,createdAt,updatedAt,headBranch > runs_icdev_rt.json
```

Two traps worth carrying forward:

* `kanban_status_transitions.recorded_at` is stored **ISO-8601 with a `T`**. A
  window bound written `'2026-09-12 02:40:00+00'` matches nothing and returns an
  empty result set that looks exactly like a quiet board.
* The Claude session's own shell runs **BelowNormal**, so a child spawned from it
  inherits BelowNormal. An arm that means to be "the interactive hand add" has to
  pass `creationflags=NORMAL_PRIORITY_CLASS` explicitly — the first run of this
  harness had every arm at BelowNormal and would have silently compared the
  priority factor against itself.

---

## Appendix — the harness, in full

### `helper.py`

```python
"""Long-lived spawner. Replicates the dispatcher's process shape.

argv: helper.py <mode>   mode = plain | belownormal | bigrss
Reads one JSON command per stdin line: {"argv": [...], "cwd": "..."}.
Writes one JSON result per stdout line. Spawns EXACTLY as
tools/genesis/reflexes/kanban.py::_create_worktree does (Popen + communicate,
start_new_session=True), so the child's parentage and inherited handles match.
"""
import json
import os
import subprocess
import sys
import time

mode = sys.argv[1]

if mode == "belownormal":
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
BALLAST = None
if mode == "bigrss":
    # touch every page so the RSS is real, not reserved
    BALLAST = bytearray(1_100_000_000)
    for i in range(0, len(BALLAST), 4096):
        BALLAST[i] = 1

sys.stdout.write(json.dumps({"ready": True, "pid": os.getpid(), "mode": mode}) + "\n")
sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line or line == "QUIT":
        break
    cmd = json.loads(line)
    t0 = time.monotonic()
    p = subprocess.Popen(cmd["argv"], cwd=cmd["cwd"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=cmd.get("timeout", 300))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        out, err, rc = "", "harness timeout", -9
    sys.stdout.write(json.dumps({
        "seconds": round(time.monotonic() - t0, 3), "rc": rc,
        "stderr": (err or "")[:300]}) + "\n")
    sys.stdout.flush()
```

### `drive2.py`

```python
"""mfx-own-08 v2: isolate WHY the runner's `git worktree add` is slower than a hand add.

Every arm runs the IDENTICAL argv the dispatcher runs
(`git -c checkout.workers=0 worktree add -b <branch> <path> origin/main`,
cwd=C:/AI/ICDev). Only ONE factor changes per arm. Arms are interleaved and the
order is ROTATED every round, because host load drifts and the first checkout of
a process pays a cold cache.

Every add is taken UNDER the shared mfx-own-06 add lock, and every load arm
starts AND stops its load strictly inside that lock, so this survey can never be
the cause of a dispatcher's park.

ARMS
  interactive    Normal priority, MY shell env, short-lived parent (the hand add)
  sched_env      Normal, the SCHEDULER'S EXACT ENVIRON, long-lived parent
  service_child  BelowNormal, scheduler environ, long-lived parent  <- the replica
  bigrss         Normal, parent holding 1.1 GB of touched RSS       (candidate 4)
  gitbusy        interactive + a loop of git commands on .git       (candidate 3)
  temp_dest      interactive, destination under %TEMP% not the repo (candidate 1)
  io_load_2      interactive + 2 processes churning small files     (load, dose 2)
  io_load_6      interactive + 6 processes churning small files     (load, dose 6)
"""
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "C:/AI/ICDev")
import psutil  # noqa: E402
from tools.coordination.gitlock import worktree_add_lock  # noqa: E402

REPO = "C:/AI/ICDev"
HERE = Path(__file__).resolve().parent
HELPER = str(HERE / "helper.py")
LOADER = str(HERE / "loader.py")
GIT_ARGV = ["git", "-c", "checkout.workers=0", "worktree", "add", "-b"]
SCHED_PID = int(sys.argv[1]) if len(sys.argv) > 1 else 23704
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
OUT = HERE / "results2.jsonl"
NORMAL = subprocess.NORMAL_PRIORITY_CLASS

SCHED_ENV = psutil.Process(SCHED_PID).environ()
MY_ENV = dict(os.environ)
TEMP_ROOT = Path(os.environ.get("TEMP", "C:/Windows/Temp")) / "icdev-worktrees" / "cli" / "mfxown08"
LOAD_ROOT = Path(os.environ.get("TEMP", "C:/Windows/Temp")) / "mfxown08-load"


def defender_cpu():
    tot = 0.0
    for p in psutil.process_iter(["name"]):
        if p.info["name"] and "MsMpEng" in p.info["name"]:
            try:
                t = p.cpu_times()
                tot += t.user + t.system
            except Exception:
                return None
    return round(tot, 3)


class Helper:
    """Long-lived spawner, so the add's parent is a service and not a shell."""

    def __init__(self, mode, env):
        self.p = subprocess.Popen([sys.executable, HELPER, mode], cwd=REPO, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1, creationflags=NORMAL)
        self.pid = json.loads(self.p.stdout.readline())["pid"]

    def run(self, argv):
        self.p.stdin.write(json.dumps({"argv": argv, "cwd": REPO}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        try:
            self.p.stdin.write("QUIT\n")
            self.p.stdin.flush()
            self.p.wait(timeout=15)
        except Exception:
            self.p.kill()


def direct(argv):
    """The hand add: short-lived parent, my env, NORMAL priority class."""
    t0 = time.monotonic()
    p = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True, creationflags=NORMAL)
    out, err = p.communicate(timeout=600)
    return {"seconds": round(time.monotonic() - t0, 3), "rc": p.returncode,
            "stderr": (err or "")[:300]}


class GitLoad:
    """Candidate 3: contention on .git itself, not the disk."""

    def __init__(self):
        self.stop = threading.Event()
        self.n = 0
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        cmds = [["git", "status", "--porcelain"], ["git", "rev-parse", "HEAD"],
                ["git", "log", "-1", "--format=%H"], ["git", "worktree", "list"],
                ["git", "diff", "--cached", "--stat"]]
        for c in itertools.cycle(cmds):
            if self.stop.is_set():
                return
            try:
                subprocess.run(c, cwd=REPO, capture_output=True, timeout=120)
                self.n += 1
            except Exception:
                pass

    def __enter__(self):
        self.t.start()
        time.sleep(1.0)
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.t.join(timeout=60)
        return False


class IOLoad:
    """Concurrent small-file churn: the shape a worker session's test run has."""

    def __init__(self, n):
        self.n = n
        self.procs = []

    def __enter__(self):
        LOAD_ROOT.mkdir(parents=True, exist_ok=True)
        for i in range(self.n):
            self.procs.append(subprocess.Popen(
                [sys.executable, LOADER, str(LOAD_ROOT / f"w{i}")],
                creationflags=NORMAL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(3.0)   # let the load reach steady state before the add starts
        return self

    def __exit__(self, *a):
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=30)
            except Exception:
                p.kill()
        shutil.rmtree(LOAD_ROOT, ignore_errors=True)
        return False


def cleanup(path, branch):
    t0 = time.monotonic()
    subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=REPO,
                   capture_output=True, timeout=900)
    if Path(path).exists():
        shutil.rmtree(path, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True, timeout=120)
    subprocess.run(["git", "branch", "-D", branch], cwd=REPO, capture_output=True, timeout=60)
    return round(time.monotonic() - t0, 1)


ARMS = ["interactive", "sched_env", "service_child", "bigrss",
        "gitbusy", "temp_dest", "io_load_2", "io_load_6"]


def main():
    helpers = {
        "sched_env": Helper("plain", SCHED_ENV),
        "service_child": Helper("belownormal", SCHED_ENV),
        "bigrss": Helper("bigrss", MY_ENV),
    }
    meta = {}
    for lbl, h in helpers.items():
        pr = psutil.Process(h.pid)
        meta[lbl] = {"pid": h.pid, "nice": pr.nice(), "rss_mb": round(pr.memory_info().rss / 1e6)}
        print(f"helper {lbl}: {meta[lbl]}", flush=True)
    trial = 0
    try:
        for rnd in range(ROUNDS):
            order = ARMS[rnd % len(ARMS):] + ARMS[:rnd % len(ARMS)]   # ROTATE
            for arm in order:
                trial += 1
                branch = f"mfxown08probe{arm.replace('_', '')}{rnd}"
                dest = (TEMP_ROOT / f"{arm}{rnd}") if arm == "temp_dest" \
                    else Path(REPO) / ".tmp" / "worktrees" / f"mfxown08probe{arm.replace('_', '')}{rnd}"
                shutil.rmtree(dest, ignore_errors=True)
                subprocess.run(["git", "branch", "-D", branch], cwd=REPO,
                               capture_output=True, timeout=60)
                argv = GIT_ARGV + [branch, str(dest), "origin/main"]
                rec = {"trial": trial, "round": rnd, "arm": arm, "dest": str(dest),
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                lock_t0 = time.monotonic()
                with worktree_add_lock(timeout=600) as held:
                    rec["lock_held"] = bool(held)
                    rec["lock_wait_s"] = round(time.monotonic() - lock_t0, 2)
                    load = None
                    if arm == "gitbusy":
                        load = GitLoad()
                    elif arm.startswith("io_load_"):
                        load = IOLoad(int(arm.rsplit("_", 1)[1]))
                    if load:
                        load.__enter__()
                    try:
                        vm = psutil.virtual_memory()
                        rec["mem_pct_before"] = vm.percent
                        rec["mem_avail_gb"] = round(vm.available / 1e9, 2)
                        d0 = defender_cpu()
                        io0 = psutil.disk_io_counters()
                        psutil.cpu_percent(interval=None)
                        if arm in helpers:
                            res = helpers[arm].run(argv)
                        else:
                            res = direct(argv)
                        rec["cpu_pct_during"] = psutil.cpu_percent(interval=None)
                        io1 = psutil.disk_io_counters()
                        d1 = defender_cpu()
                    finally:
                        if load:
                            if isinstance(load, GitLoad):
                                rec["git_load_ops"] = load.n
                            load.__exit__()
                    rec.update(res)
                    rec["defender_cpu_s"] = (None if d0 is None or d1 is None
                                             else round(d1 - d0, 3))
                    rec["read_mb"] = round((io1.read_bytes - io0.read_bytes) / 1e6, 1)
                    rec["write_mb"] = round((io1.write_bytes - io0.write_bytes) / 1e6, 1)
                rec["cleanup_s"] = cleanup(dest, branch)
                with OUT.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
                print(f"r{rnd} {arm:14s} {rec['seconds']:7.2f}s rc={rec['rc']} "
                      f"def={rec['defender_cpu_s']} rd={rec['read_mb']} wr={rec['write_mb']}MB "
                      f"cpu={rec['cpu_pct_during']}% mem={rec['mem_pct_before']}% "
                      f"lock={rec['lock_wait_s']}s clean={rec['cleanup_s']}s", flush=True)
    finally:
        for h in helpers.values():
            h.close()
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
        shutil.rmtree(LOAD_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
```

### `loader.py`

```python
"""One unit of small-file I/O churn: the shape a worker session's test run has.

Writes 4 KiB files in batches, fsync-free (as a test run's temp writes are),
re-reads them, then deletes the batch and repeats until terminated. Small-file
create/delete is what contends on NTFS metadata and on Defender's on-access
scanner -- a single large sequential write does not reproduce it.
"""
import os
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
payload = os.urandom(4096)
batch = 400
i = 0
while True:
    d = root / f"b{i % 8}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    for n in range(batch):
        (d / f"f{n}.tmp").write_bytes(payload)
    for n in range(batch):
        (d / f"f{n}.tmp").read_bytes()
    i += 1
```

### `drive3.py`

```python
"""mfx-own-08 v3: the load arm that matters -- I/O from INSIDE WSL/docker.

The retrospective half says every killed add sat under a self-hosted icdev_ft /
icdev_rt CI job, and those jobs execute in containers whose writes land on the
WSL vhdx on this same physical disk. v2 loaded the host from the WINDOWS side
and barely moved the add (3.4s -> 6.0s at six churners). This arm loads it from
the side the runners actually use.

Three arms, interleaved, order rotated per round:
  interactive   no load                          (the hand add)
  win_load      6 Windows small-file churners    (v2's heaviest, repeated here
                                                  so the two loads are compared
                                                  in the SAME session)
  wsl_load      3 containers writing to the WSL vhdx

Every add is taken under the shared mfx-own-06 add lock, and each load starts
and stops strictly inside it.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "C:/AI/ICDev")
import psutil  # noqa: E402
from tools.coordination.gitlock import worktree_add_lock  # noqa: E402

REPO = "C:/AI/ICDev"
HERE = Path(__file__).resolve().parent
LOADER = str(HERE / "loader.py")
GIT_ARGV = ["git", "-c", "checkout.workers=0", "worktree", "add", "-b"]
NORMAL = subprocess.NORMAL_PRIORITY_CLASS
OUT = HERE / "results3.jsonl"
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
LOAD_ROOT = Path(__import__("os").environ.get("TEMP", "C:/Windows/Temp")) / "mfxown08-load3"
IMAGE = "nginx:alpine"
CNAME = "mfxown08-load"
# sustained write + small-file churn, the two shapes a CI job produces
CSCRIPT = ("while true; do dd if=/dev/zero of=/tmp/big bs=1M count=512 conv=fsync "
           "2>/dev/null; i=0; while [ $i -lt 300 ]; do echo x > /tmp/f$i; i=$((i+1)); "
           "done; rm -f /tmp/big /tmp/f*; done")


def defender_cpu():
    tot = 0.0
    for p in psutil.process_iter(["name"]):
        if p.info["name"] and "MsMpEng" in p.info["name"]:
            try:
                t = p.cpu_times()
                tot += t.user + t.system
            except Exception:
                return None
    return round(tot, 3)


def direct(argv):
    t0 = time.monotonic()
    p = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True, creationflags=NORMAL)
    out, err = p.communicate(timeout=900)
    return {"seconds": round(time.monotonic() - t0, 3), "rc": p.returncode,
            "stderr": (err or "")[:200]}


class WinLoad:
    def __init__(self, n=6):
        self.n, self.procs = n, []

    def __enter__(self):
        LOAD_ROOT.mkdir(parents=True, exist_ok=True)
        for i in range(self.n):
            self.procs.append(subprocess.Popen(
                [sys.executable, LOADER, str(LOAD_ROOT / f"w{i}")], creationflags=NORMAL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(5.0)
        return self

    def __exit__(self, *a):
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=30)
            except Exception:
                p.kill()
        shutil.rmtree(LOAD_ROOT, ignore_errors=True)
        return False


class WslLoad:
    def __init__(self, n=3):
        self.n, self.names = n, []

    def __enter__(self):
        for i in range(self.n):
            name = f"{CNAME}{i}"
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=120)
            r = subprocess.run(["docker", "run", "-d", "--rm", "--name", name,
                                "--entrypoint", "sh", IMAGE, "-c", CSCRIPT],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                self.names.append(name)
            else:
                print("  docker run failed:", (r.stderr or "").strip()[:150], flush=True)
        time.sleep(10.0)   # let the vhdx writes reach steady state
        return self

    def __exit__(self, *a):
        for n in self.names:
            subprocess.run(["docker", "rm", "-f", n], capture_output=True, timeout=180)
        return False


def cleanup(path, branch):
    t0 = time.monotonic()
    subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=REPO,
                   capture_output=True, timeout=900)
    if Path(path).exists():
        shutil.rmtree(path, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True, timeout=120)
    subprocess.run(["git", "branch", "-D", branch], cwd=REPO, capture_output=True, timeout=60)
    return round(time.monotonic() - t0, 1)


ARMS = ["interactive", "win_load", "wsl_load"]


def main():
    trial = 0
    try:
        for rnd in range(ROUNDS):
            for arm in ARMS[rnd % len(ARMS):] + ARMS[:rnd % len(ARMS)]:
                trial += 1
                branch = f"mfxown08l{arm.replace('_', '')}{rnd}"
                dest = Path(REPO) / ".tmp" / "worktrees" / branch
                shutil.rmtree(dest, ignore_errors=True)
                subprocess.run(["git", "branch", "-D", branch], cwd=REPO,
                               capture_output=True, timeout=60)
                argv = GIT_ARGV + [branch, str(dest), "origin/main"]
                rec = {"trial": trial, "round": rnd, "arm": arm,
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                with worktree_add_lock(timeout=600) as held:
                    rec["lock_held"] = bool(held)
                    load = {"win_load": WinLoad, "wsl_load": WslLoad}.get(arm)
                    ctx = load() if load else None
                    if ctx:
                        ctx.__enter__()
                    try:
                        vm = psutil.virtual_memory()
                        rec["mem_pct_before"] = vm.percent
                        d0, io0 = defender_cpu(), psutil.disk_io_counters()
                        psutil.cpu_percent(interval=None)
                        res = direct(argv)
                        rec["cpu_pct_during"] = psutil.cpu_percent(interval=None)
                        io1, d1 = psutil.disk_io_counters(), defender_cpu()
                    finally:
                        if ctx:
                            ctx.__exit__()
                    rec.update(res)
                    rec["defender_cpu_s"] = None if None in (d0, d1) else round(d1 - d0, 3)
                    rec["read_mb"] = round((io1.read_bytes - io0.read_bytes) / 1e6, 1)
                    rec["write_mb"] = round((io1.write_bytes - io0.write_bytes) / 1e6, 1)
                rec["cleanup_s"] = cleanup(dest, branch)
                with OUT.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
                print(f"r{rnd} {arm:12s} {rec['seconds']:7.2f}s rc={rec['rc']} "
                      f"def={rec['defender_cpu_s']} wr={rec['write_mb']}MB "
                      f"cpu={rec['cpu_pct_during']}% mem={rec['mem_pct_before']}% "
                      f"clean={rec['cleanup_s']}s", flush=True)
    finally:
        for i in range(6):
            subprocess.run(["docker", "rm", "-f", f"{CNAME}{i}"], capture_output=True, timeout=120)
        shutil.rmtree(LOAD_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
```

### `sweeplat.py`

```python
"""Host I/O speed from OUR OWN LOG, with the CALLER and the WORKLOAD held constant.

`_sweep_old_worktrees` walks the same ~50 registered worktrees every cycle and
logs one `Sweep: keeping <path>` line per worktree, so the gap between two
consecutive lines of one pass IS the `git status` latency for that worktree.
Same caller (a dispatcher), same command, same directory, over and over.

So a pass's slowness is a measurement of THE HOST at that minute, with the two
variables this card is about -- who the caller is, and what it is doing --
held fixed by construction.

  step latency          gap between consecutive lines within one pass
  per-path normalising  each path's own median over all passes = 1.0, because
                        a 20k-file worktree and an unreadable one are not
                        comparable in absolute seconds
  pass factor           median over the paths in that pass of (latency / that
                        path's median). 1.0 = this host's normal speed.
"""
import glob
import json
import statistics
import sys
from datetime import datetime, timedelta

GAP = timedelta(seconds=90)   # a gap this long means a new pass, not a slow step


def parse():
    sweeps, adds = [], []
    for f in sorted(glob.glob(".logs/tools.genesis.reflexes.kanban.ndjson*")):
        for line in open(f, encoding="utf-8", errors="replace"):
            if "Sweep: keeping" not in line and "Created worktree for" not in line \
                    and "exceeded its 30s budget" not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = datetime.fromisoformat(r["ts"])
            m = r["message"]
            if m.startswith("Sweep: keeping "):
                sweeps.append((ts, m.split("Sweep: keeping ", 1)[1].split(" -- ")[0]))
            elif "Created worktree for" in m:
                import re
                mm = re.search(r"Created worktree for (\S+) .* in ([\d.]+)s", m)
                if mm:
                    adds.append((ts, mm.group(1), float(mm.group(2)), "ok"))
            else:
                import re
                mm = re.search(r"add for (\S+) exceeded its \d+s budget after ([\d.]+)s", m)
                if mm:
                    adds.append((ts, mm.group(1), float(mm.group(2)), "killed"))
    sweeps.sort()
    adds.sort()
    return sweeps, adds


def passes(sweeps):
    """Split the sweep lines into contiguous passes, de-duplicating the two
    dispatchers that write to this one file."""
    out, cur = [], []
    prev = None
    for ts, path in sweeps:
        if prev is not None and ts - prev > GAP:
            if len(cur) > 3:
                out.append(cur)
            cur = []
        # both dispatchers log; a repeat of the same path inside one pass is the
        # other process's copy of the same walk, not a second measurement
        if cur and any(p == path for _, p in cur[-3:]):
            prev = ts
            continue
        cur.append((ts, path))
        prev = ts
    if len(cur) > 3:
        out.append(cur)
    return out


def main():
    sweeps, adds = parse()
    ps = passes(sweeps)
    # step latency per (pass, path)
    steps = []          # (pass_index, path, seconds, ts)
    for i, p in enumerate(ps):
        for (t0, _), (t1, path) in zip(p, p[1:]):
            steps.append((i, path, (t1 - t0).total_seconds(), t1))
    base = {}
    for path in {s[1] for s in steps}:
        v = sorted(s[2] for s in steps if s[1] == path)
        if len(v) >= 5:
            base[path] = statistics.median(v)
    rows = []
    for i, p in enumerate(ps):
        fac = [s[2] / base[s[1]] for s in steps
               if s[0] == i and s[1] in base and base[s[1]] > 0]
        if len(fac) >= 5:
            rows.append({"pass": i, "start": p[0][0], "end": p[-1][0],
                         "n": len(fac), "factor": round(statistics.median(fac), 2),
                         "total_s": round((p[-1][0] - p[0][0]).total_seconds(), 1)})
    print(f"sweep passes: {len(rows)}   paths with a baseline: {len(base)}   "
          f"steps: {len(steps)}")
    facs = sorted(r["factor"] for r in rows)
    print(f"pass factor  p10={facs[len(facs)//10]:.2f}  median={statistics.median(facs):.2f}  "
          f"p90={facs[len(facs)*9//10]:.2f}  max={facs[-1]:.2f}")

    print("\n== every add, with the host speed measured AROUND it ==")
    print(f"{'add utc':20s} {'dur':>7s} {'st':7s} {'factor':>7s} {'lag_min':>8s}  task")
    out = []
    for ts, task, dur, st in adds:
        near = [r for r in rows if abs((r["start"] - ts).total_seconds()) < 900
                or (r["start"] <= ts <= r["end"])]
        if near:
            r = min(near, key=lambda r: min(abs((r["start"] - ts).total_seconds()),
                                            abs((r["end"] - ts).total_seconds())))
            lag = round(min((r["start"] - ts).total_seconds(),
                            (r["end"] - ts).total_seconds(), key=abs) / 60.0, 1)
            out.append((ts, task, dur, st, r["factor"], lag))
            print(f"{str(ts)[:19]:20s} {dur:6.1f}s {st:7s} {r['factor']:7.2f} {lag:8.1f}  {task}")
        else:
            out.append((ts, task, dur, st, None, None))
            print(f"{str(ts)[:19]:20s} {dur:6.1f}s {st:7s} {'--':>7s} {'--':>8s}  {task}")

    meas = [o for o in out if o[4] is not None]
    print(f"\nadds with a host-speed measurement within 15 min: {len(meas)} of {len(out)}")
    if meas:
        slow = [o for o in meas if o[2] >= 20.0]
        fast = [o for o in meas if o[2] < 10.0]
        for label, grp in (("adds >= 20s", slow), ("adds < 10s", fast)):
            if grp:
                f = sorted(o[4] for o in grp)
                print(f"  {label:12s} n={len(grp):3d}  host factor median={statistics.median(f):5.2f}"
                      f"  min={f[0]:.2f} max={f[-1]:.2f}")
        try:
            import math
            xs = [o[4] for o in meas]
            ys = [o[2] for o in meas]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
            print(f"  pearson r(add duration, host factor) = {num/den:.3f}  n={len(xs)}")
        except Exception as e:
            print("  correlation unavailable:", e)

    print("\n== slowest 12 sweep passes (host at its worst) ==")
    for r in sorted(rows, key=lambda r: -r["factor"])[:12]:
        print(f"  {str(r['start'])[:19]}  factor={r['factor']:5.2f}  "
              f"pass_total={r['total_s']:6.1f}s  steps={r['n']}")


if __name__ == "__main__":
    main()
```

### `correlate.py`

```python
"""Does the HOST's speed just BEFORE an add explain its duration -- and what
makes the host slow?

Two series, both re-derived from primary records and neither of them from the
add durations themselves:

  host factor   from `Sweep: keeping` step latencies (sweeplat.py): the same
                caller running the same walk over the same worktrees, so the
                only thing left varying is the host. Only a pass that ENDED
                BEFORE the add STARTED is used, so the add cannot be measuring
                a host it is itself loading.

  workers       tasks `in_progress` on the board at that instant, replayed from
                kanban_status_transitions. Each one is a Claude worker session
                the DISPATCHER ITSELF spawned, running pytest / npm / git in a
                275 MB worktree.
"""
import glob
import json
import math
import re
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "C:/AI/ICDev")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("C:/AI/ICDev/.env", override=True)
from tools.db.storage import get_connection  # noqa: E402

GAP = timedelta(seconds=90)
LOOKBACK = 900        # a host measurement older than this is not about this add
RUNNING = {"in_progress"}


def parse_log():
    sweeps, adds = [], []
    for f in sorted(glob.glob("C:/AI/ICDev/.logs/tools.genesis.reflexes.kanban.ndjson*")):
        for line in open(f, encoding="utf-8", errors="replace"):
            if ("Sweep: keeping" not in line and "Created worktree for" not in line
                    and "exceeded its 30s budget" not in line):
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts, m = datetime.fromisoformat(r["ts"]), r["message"]
            if m.startswith("Sweep: keeping "):
                sweeps.append((ts, m.split("Sweep: keeping ", 1)[1].split(" -- ")[0]))
                continue
            mm = re.search(r"Created worktree for (\S+) .* in ([\d.]+)s", m)
            if mm:
                adds.append((ts, mm.group(1), float(mm.group(2)), "ok"))
                continue
            mm = re.search(r"add for (\S+) exceeded its \d+s budget after ([\d.]+)s", m)
            if mm:
                adds.append((ts, mm.group(1), float(mm.group(2)), "killed"))
    sweeps.sort()
    adds.sort()
    return sweeps, adds


def passes(sweeps):
    out, cur, prev = [], [], None
    for ts, path in sweeps:
        if prev is not None and ts - prev > GAP:
            if len(cur) > 3:
                out.append(cur)
            cur = []
        if cur and any(p == path for _, p in cur[-3:]):
            prev = ts
            continue
        cur.append((ts, path))
        prev = ts
    if len(cur) > 3:
        out.append(cur)
    return out


def host_factors(ps):
    steps = []
    for i, p in enumerate(ps):
        for (t0, _), (t1, path) in zip(p, p[1:]):
            steps.append((i, path, (t1 - t0).total_seconds()))
    base = {}
    for path in {s[1] for s in steps}:
        v = sorted(s[2] for s in steps if s[1] == path)
        if len(v) >= 5:
            base[path] = statistics.median(v)
    rows = []
    for i, p in enumerate(ps):
        fac = [s[2] / base[s[1]] for s in steps if s[0] == i and base.get(s[1], 0) > 0]
        if len(fac) >= 5:
            rows.append({"start": p[0][0], "end": p[-1][0], "n": len(fac),
                         "factor": statistics.median(fac)})
    return rows


def worker_timeline():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT task_id, from_status, to_status, recorded_at "
                "FROM kanban_status_transitions ORDER BY recorded_at")
    ev = []
    for r in cur.fetchall():
        t = r["task_id"] if hasattr(r, "keys") else r[0]
        f = r["from_status"] if hasattr(r, "keys") else r[1]
        s = r["to_status"] if hasattr(r, "keys") else r[2]
        ts = r["recorded_at"] if hasattr(r, "keys") else r[3]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        ev.append((ts, t, f, s))
    conn.close()
    return ev


def worker_intervals(ev):
    """[start, end) of every `-> in_progress` run, from the transition log.

    Counting `to_status` and subtracting `from_status` does NOT work: a task can
    leave in_progress through a transition the log records from a different
    state, so the naive counter only ever rises (it read 469 -> 487 "concurrent
    workers", which is a cumulative total wearing a concurrency label). An
    INTERVAL per task is the only reading the log actually supports.
    """
    by_task = {}
    for ts, t, f, s in ev:
        by_task.setdefault(t, []).append((ts, s))
    iv = []
    for t, rows in by_task.items():
        rows.sort()
        for i, (ts, s) in enumerate(rows):
            if s not in RUNNING:
                continue
            end = rows[i + 1][0] if i + 1 < len(rows) else None
            iv.append((ts, end, t))
    return iv


def workers_at(iv, when):
    """Tasks in an in_progress interval at `when`. An interval with no recorded
    end is capped at 4h -- a worker session is not still running a week later,
    and letting it run forever is the same defect as the cumulative counter."""
    from datetime import timedelta as _td
    n = 0
    for start, end, _t in iv:
        if start <= when and (end or start + _td(hours=4)) > when:
            n += 1
    return n


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def main():
    sweeps, adds = parse_log()
    rows = host_factors(passes(sweeps))
    ev = worker_timeline()
    iv = worker_intervals(ev)
    print(f"adds={len(adds)}  sweep passes with a factor={len(rows)}  "
          f"board transitions={len(ev)}\n")

    data = []
    for ts, task, dur, st in adds:
        start = ts - timedelta(seconds=dur)
        prior = [r for r in rows if r["end"] <= start
                 and (start - r["end"]).total_seconds() <= LOOKBACK]
        f = max(prior, key=lambda r: r["end"])["factor"] if prior else None
        lag = round((start - max(prior, key=lambda r: r["end"])["end"]).total_seconds() / 60, 1) \
            if prior else None
        w = workers_at(iv, start)
        data.append({"ts": ts, "task": task, "dur": dur, "st": st,
                     "factor": f, "lag": lag, "workers": w})

    print(f"{'add start utc':20s} {'dur':>7s} {'st':7s} {'host':>6s} {'lag':>5s} "
          f"{'wrk':>4s}  task")
    for d in data[-30:]:
        fs = f"{d['factor']:6.2f}" if d["factor"] is not None else "    --"
        ls = f"{d['lag']:5.1f}" if d["lag"] is not None else "   --"
        print(f"{str(d['ts'])[:19]:20s} {d['dur']:6.1f}s {d['st']:7s} {fs} {ls} "
              f"{d['workers']:4d}  {d['task']}")

    meas = [d for d in data if d["factor"] is not None]
    print(f"\nadds with a PRIOR host measurement (<= {LOOKBACK//60} min before the "
          f"add started): {len(meas)} of {len(data)}")
    if not meas:
        return
    slow = [d for d in meas if d["dur"] >= 20]
    fast = [d for d in meas if d["dur"] < 10]
    for lbl, g in (("add >= 20s", slow), ("add <  10s", fast)):
        f = sorted(d["factor"] for d in g)
        w = sorted(d["workers"] for d in g)
        print(f"  {lbl}: n={len(g):3d}  host factor median={statistics.median(f):5.2f} "
              f"[{f[0]:.2f}..{f[-1]:.2f}]   workers median={statistics.median(w):.1f} "
              f"[{w[0]}..{w[-1]}]")
    print(f"  spearman(duration, host factor) = "
          f"{spearman([d['factor'] for d in meas], [d['dur'] for d in meas]):.3f}")
    print(f"  spearman(duration, workers)     = "
          f"{spearman([float(d['workers']) for d in meas], [d['dur'] for d in meas]):.3f}")
    print(f"  spearman(host factor, workers)  = "
          f"{spearman([float(d['workers']) for d in meas], [d['factor'] for d in meas]):.3f}")

    print("\n== contingency: does a slow host precede a slow add? ==")
    for thr in (1.5, 2.0, 3.0):
        hi = [d for d in meas if d["factor"] >= thr]
        lo = [d for d in meas if d["factor"] < thr]
        def pct(g):
            return (100.0 * sum(1 for d in g if d["dur"] >= 20) / len(g)) if g else None
        ph, pl = pct(hi), pct(lo)
        print(f"  host factor >= {thr}:  n={len(hi):3d}  P(add >= 20s)="
              f"{'None' if ph is None else f'{ph:5.1f}%'}"
              f"    < {thr}: n={len(lo):3d}  P="
              f"{'None' if pl is None else f'{pl:5.1f}%'}")

    print("\n== by concurrent worker sessions (the dispatcher's own doing) ==")
    for k in sorted({d["workers"] for d in meas}):
        g = [d for d in meas if d["workers"] == k]
        du = sorted(d["dur"] for d in g)
        fa = sorted(d["factor"] for d in g)
        print(f"  workers={k}: n={len(g):3d}  add median={statistics.median(du):6.1f}s "
              f"[{du[0]:.1f}..{du[-1]:.1f}]   host factor median={statistics.median(fa):5.2f}")

    allw = [d for d in data]
    print("\n== workers vs add duration over ALL adds (no host measurement needed) ==")
    for k in sorted({d["workers"] for d in allw}):
        g = [d for d in allw if d["workers"] == k]
        du = sorted(d["dur"] for d in g)
        kl = sum(1 for d in g if d["st"] == "killed")
        print(f"  workers={k}: n={len(g):3d}  median={statistics.median(du):6.1f}s "
              f"max={du[-1]:5.1f}s  killed={kl} ({100.0*kl/len(g):.1f}%)")
    print(f"  spearman(duration, workers) over all adds = "
          f"{spearman([float(d['workers']) for d in allw], [d['dur'] for d in allw]):.3f}")


if __name__ == "__main__":
    main()
```

### `ci_overlap.py`

```python
"""Is the host's slow window a SELF-HOSTED CI job?

ICDEV's own CI runs on GitHub-hosted runners (`runs-on: ubuntu-latest` /
`windows-latest`), so it cannot load this machine. icdev_ft and icdev_rt run on
FOUR self-hosted runner containers on THIS host (icdev-ft-runner, -2, -3,
icdev-rt-runner), so their jobs are local disk and local CPU.

`createdAt -> updatedAt` is the run's wall window as the forge records it. It is
an UPPER bound on the busy period (a queued job occupies the window without
loading anything) and it is all the forge exposes cheaply, so it is labelled a
window and never a busy period.
"""
import glob
import json
import math
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from correlate import host_factors, parse_log, passes, spearman  # noqa: E402

HERE = __import__("pathlib").Path(__file__).resolve().parent


def ci_windows():
    out = []
    for repo in ("icdev_ft", "icdev_rt"):
        p = HERE / f"runs_{repo}.json"
        if not p.exists():
            continue
        for r in json.loads(p.read_text(encoding="utf-8")):
            try:
                a = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
                b = datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00"))
            except Exception:
                continue
            if b > a:
                out.append((a, b, repo))
    return sorted(out)


def overlap(win, t0, t1):
    """How many self-hosted CI runs had a window overlapping [t0, t1]."""
    return sum(1 for a, b, _ in win if a < t1 and b > t0)


def main():
    sweeps, adds = parse_log()
    rows = host_factors(passes(sweeps))
    win = ci_windows()
    if not win:
        print("UNMEASURABLE: no self-hosted CI history was readable")
        return
    lo = min(a for a, _, _ in win)
    print(f"self-hosted CI runs: {len(win)}  (ft+rt), earliest window start {lo}")
    rows = [r for r in rows if r["start"] >= lo]
    adds = [a for a in adds if a[0] >= lo]
    print(f"sweep passes inside that history: {len(rows)}   adds: {len(adds)}\n")

    xs, ys = [], []
    for r in rows:
        n = overlap(win, r["start"], r["end"])
        xs.append(float(n))
        ys.append(r["factor"])
    print("== sweep-pass host factor vs concurrent self-hosted CI runs ==")
    for k in sorted(set(int(x) for x in xs)):
        g = [y for x, y in zip(xs, ys) if int(x) == k]
        print(f"  ci_runs={k}: n={len(g):3d}  host factor median={statistics.median(g):5.2f} "
              f"max={max(g):5.2f}")
    print(f"  spearman(host factor, concurrent CI runs) = {spearman(xs, ys):.3f}  n={len(xs)}")

    print("\n== add duration vs concurrent self-hosted CI runs ==")
    ax, ay = [], []
    for ts, task, dur, st in adds:
        start = ts - timedelta(seconds=dur)
        n = overlap(win, start, ts)
        ax.append(float(n))
        ay.append(dur)
    for k in sorted(set(int(x) for x in ax)):
        g = [(y, s) for x, y, s in zip(ax, ay, [a[3] for a in adds]) if int(x) == k]
        d = sorted(v for v, _ in g)
        kl = sum(1 for _, s in g if s == "killed")
        print(f"  ci_runs={k}: n={len(g):3d}  add median={statistics.median(d):6.1f}s "
              f"max={d[-1]:5.1f}s  killed={kl} ({100.0*kl/len(g):.1f}%)")
    print(f"  spearman(add duration, concurrent CI runs) = {spearman(ax, ay):.3f}  n={len(ax)}")

    print("\n== the incident window, minute by minute ==")
    t0 = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 12, 4, 10, tzinfo=timezone.utc)
    for a, b, repo in win:
        if a < t1 and b > t0:
            print(f"  {repo:9s} {str(a)[:19]} -> {str(b)[:19]}")
    print("  sweep passes:")
    for r in rows:
        if t0 <= r["start"] <= t1:
            print(f"    {str(r['start'])[:19]} -> {str(r['end'])[:19]}  factor={r['factor']:5.2f}")
    print("  adds:")
    for ts, task, dur, st in adds:
        if t0 <= ts <= t1:
            print(f"    {str(ts)[:19]}  {dur:5.1f}s {st:7s} {task}")


if __name__ == "__main__":
    main()
```

### `runner_jobs.py`

```python
"""What was EXECUTING on this host's self-hosted runners while each add ran?

The forge's `createdAt -> updatedAt` window includes QUEUE time, so it is an
upper bound on the busy period. The runner containers write their own
`Running job: X` / `Job X completed` lines with timestamps, which is the machine
saying what it was actually doing. That is the evidence this uses.

Its reach is the containers' uptime -- `docker logs` holds nothing from before
the current container started -- so an add older than that is UNMEASURABLE here,
never "no job was running".
"""
import glob
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from correlate import parse_log  # noqa: E402

RUNNERS = ["icdev-ft-runner", "icdev-ft-runner-2", "icdev-ft-runner-3", "icdev-rt-runner"]


def jobs():
    out, horizon = [], None
    for c in RUNNERS:
        r = subprocess.run(["docker", "logs", "--timestamps", c],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print(f"  {c}: unreadable ({(r.stderr or '').strip()[:80]})")
            continue
        lines = (r.stdout or "") + (r.stderr or "")
        first = None
        open_job = None
        for line in lines.splitlines():
            m = re.match(r"(\S+Z)\s+\S+\s+\S+\s*(.*)$", line)
            if not m:
                continue
            try:
                ts = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            except Exception:
                continue
            if first is None or ts < first:
                first = ts
            body = m.group(2)
            jm = re.search(r"Running job: (.+)$", body)
            if jm:
                open_job = (ts, jm.group(1).strip())
                continue
            dm = re.search(r"Job (.+) completed with result", body)
            if dm and open_job:
                out.append((open_job[0], ts, c, open_job[1]))
                open_job = None
        if open_job:
            out.append((open_job[0], datetime.now(timezone.utc), c, open_job[1] + " (running)"))
        if first and (horizon is None or first > horizon):
            horizon = first
        print(f"  {c}: log starts {str(first)[:19]}")
    return sorted(out), horizon


def main():
    print("runner container logs:")
    js, horizon = jobs()
    if not js:
        print("UNMEASURABLE: no runner job history")
        return
    print(f"\njobs recorded: {len(js)}   common horizon (all runners readable from): "
          f"{str(horizon)[:19]}\n")
    _, adds = parse_log()
    adds = [a for a in adds if a[0] - timedelta(seconds=a[2]) >= horizon]
    print(f"adds inside that horizon: {len(adds)}\n")
    print(f"{'add start utc':20s} {'dur':>7s} {'st':7s} {'jobs':>5s}  concurrent job(s)")
    rows = []
    for ts, task, dur, st in adds:
        t0 = ts - timedelta(seconds=dur)
        cur = [(a, b, c, n) for a, b, c, n in js if a < ts and b > t0]
        rows.append((ts, task, dur, st, cur))
        names = ", ".join(sorted({f"{n}@{c.split('-')[-1]}" for _, _, c, n in cur})) or "-"
        print(f"{str(t0)[:19]:20s} {dur:6.1f}s {st:7s} {len(cur):5d}  {names[:90]}")

    print("\n== add duration by number of jobs EXECUTING on this host ==")
    for k in sorted({len(r[4]) for r in rows}):
        g = [r for r in rows if len(r[4]) == k]
        d = sorted(r[2] for r in g)
        kl = sum(1 for r in g if r[3] == "killed")
        print(f"  jobs={k}: n={len(g):3d}  median={statistics.median(d):6.1f}s "
              f"max={d[-1]:5.1f}s  killed={kl} ({100.0*kl/len(g):.1f}%)")

    busy = [r for r in rows if r[4]]
    idle = [r for r in rows if not r[4]]
    def med(g):
        return statistics.median([r[2] for r in g]) if g else None
    print(f"\n  a job executing : n={len(busy):3d}  median="
          f"{'None' if med(busy) is None else f'{med(busy):.1f}s'}  "
          f"killed={sum(1 for r in busy if r[3]=='killed')}")
    print(f"  no job executing: n={len(idle):3d}  median="
          f"{'None' if med(idle) is None else f'{med(idle):.1f}s'}  "
          f"killed={sum(1 for r in idle if r[3]=='killed')}")

    # Fisher exact, two-tailed, on killed x (job executing)
    a = sum(1 for r in busy if r[3] == "killed")
    b = len(busy) - a
    c = sum(1 for r in idle if r[3] == "killed")
    d = len(idle) - c
    from math import comb
    n = a + b + c + d
    if n and (a + b) and (c + d):
        def p(x):
            return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)
        obs = p(a)
        tot = sum(p(x) for x in range(0, min(a + b, a + c) + 1)
                  if p(x) <= obs + 1e-12)
        print(f"  fisher exact (killed x job executing) 2-tailed p = {tot:.2e}  "
              f"table=[[{a},{b}],[{c},{d}]]")


if __name__ == "__main__":
    main()
```

### `final_numbers.py`

```python
"""Every figure quoted in docs/audits/mfx-own-08-*.md, from one run."""
import json
import statistics
import sys
from datetime import datetime, timedelta
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ci_overlap import ci_windows, overlap  # noqa: E402
from correlate import host_factors, parse_log, passes, spearman  # noqa: E402


def fisher(a, b, c, d):
    n = a + b + c + d
    def p(x):
        return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)
    obs = p(a)
    return sum(p(x) for x in range(0, min(a + b, a + c) + 1) if p(x) <= obs + 1e-12)


def arm_table(path, arms):
    rows = [json.loads(l) for l in open(HERE / path, encoding="utf-8")]
    print(f"\n--- {path}: {len(rows)} trials, all rc=0: {all(r['rc'] == 0 for r in rows)} ---")
    base = statistics.median([r["seconds"] for r in rows if r["arm"] == arms[0]])
    for arm in arms:
        g = sorted([r for r in rows if r["arm"] == arm], key=lambda r: r["round"])
        d = [r["seconds"] for r in g]
        df = [r["defender_cpu_s"] for r in g]
        print(f"  {arm:14s} n={len(d)} median={statistics.median(d):5.2f}s "
              f"[{min(d):.2f}..{max(d):.2f}]  x{statistics.median(d)/base:4.2f}  "
              f"defender_cpu median={statistics.median(df):5.1f}s  "
              f"{[f'{x:.2f}' for x in d]}")
    return rows


def main():
    print("=" * 78)
    print("A. PROSPECTIVE -- does any CALLER-SIDE factor reproduce the gap?")
    print("=" * 78)
    arm_table("results2.jsonl", ["interactive", "sched_env", "service_child", "bigrss",
                                 "gitbusy", "temp_dest", "io_load_2", "io_load_6"])
    arm_table("results3.jsonl", ["interactive", "win_load", "wsl_load"])

    print("\n" + "=" * 78)
    print("B. RETROSPECTIVE -- the host, measured with the CALLER held constant")
    print("=" * 78)
    sweeps, adds = parse_log()
    rows = host_factors(passes(sweeps))
    print(f"  adds recorded: {len(adds)}  killed: {sum(1 for a in adds if a[3]=='killed')}"
          f"  ({100.0*sum(1 for a in adds if a[3]=='killed')/len(adds):.2f}%)")
    print(f"  sweep passes with a host factor: {len(rows)}")
    f = sorted(r["factor"] for r in rows)
    print(f"  host factor p10={f[len(f)//10]:.2f} median={statistics.median(f):.2f} "
          f"p90={f[len(f)*9//10]:.2f} max={f[-1]:.2f}")
    meas = []
    for ts, task, dur, st in adds:
        start = ts - timedelta(seconds=dur)
        prior = [r for r in rows if r["end"] <= start
                 and (start - r["end"]).total_seconds() <= 900]
        if prior:
            meas.append((dur, st, max(prior, key=lambda r: r["end"])["factor"]))
    print(f"  adds with a host measurement taken BEFORE they started: {len(meas)}")
    for lbl, g in (("add >= 20s", [m for m in meas if m[0] >= 20]),
                   ("add <  10s", [m for m in meas if m[0] < 10])):
        v = sorted(m[2] for m in g)
        print(f"    {lbl}: n={len(g):3d} host factor median={statistics.median(v):5.2f} "
              f"[{v[0]:.2f}..{v[-1]:.2f}]")
    print(f"  spearman(add duration, prior host factor) = "
          f"{spearman([m[2] for m in meas], [m[0] for m in meas]):.3f}  n={len(meas)}")

    print("\n" + "=" * 78)
    print("C. ATTRIBUTION -- self-hosted CI on this same host")
    print("=" * 78)
    win = ci_windows()
    print(f"  self-hosted runs readable (icdev_ft + icdev_rt): {len(win)}, "
          f"from {str(min(a for a,_,_ in win))[:19]}")
    tab = {"ci": [], "noci": []}
    for ts, task, dur, st in adds:
        start = ts - timedelta(seconds=dur)
        tab["ci" if overlap(win, start, ts) else "noci"].append((dur, st))
    for k in ("ci", "noci"):
        g = tab[k]
        d = sorted(x[0] for x in g)
        kl = sum(1 for x in g if x[1] == "killed")
        sl = sum(1 for x in g if x[0] >= 20)
        print(f"  {k:5s}: n={len(g):3d}  median={statistics.median(d):6.1f}s "
              f"max={d[-1]:5.1f}s  killed={kl} ({100.0*kl/len(g):5.1f}%)  "
              f">=20s={sl} ({100.0*sl/len(g):5.1f}%)")
    a = sum(1 for x in tab["ci"] if x[1] == "killed")
    b = len(tab["ci"]) - a
    c = sum(1 for x in tab["noci"] if x[1] == "killed")
    d_ = len(tab["noci"]) - c
    print(f"  killed x CI-in-flight = [[{a},{b}],[{c},{d_}]]  fisher 2-tailed p="
          f"{fisher(a, b, c, d_):.3e}")
    a = sum(1 for x in tab["ci"] if x[0] >= 20)
    b = len(tab["ci"]) - a
    c = sum(1 for x in tab["noci"] if x[0] >= 20)
    d_ = len(tab["noci"]) - c
    print(f"  >=20s  x CI-in-flight = [[{a},{b}],[{c},{d_}]]  fisher 2-tailed p="
          f"{fisher(a, b, c, d_):.3e}")


if __name__ == "__main__":
    main()
```
