# CUI // SP-CTI
"""Drive a generated fuzz harness, coverage-guided (icx-ent-01). Runs inside the sandbox only.

Usage (in the container): python /harness/fuzz_runner.py /harness/<spec>.json
Writes /out/<spec id>.json: {"engine", "crashed", "crash_input" (base64), "exception", "iterations", "coverage",
"corpus"}.

Two engines, the same harness (``TestOneInput(text)``, generated per entry point by tools/vulnresearch/harness.py):

* ``atheris`` (libFuzzer, coverage-guided) when it is importable in the image (deploy/vuln-sandbox, pinned);
* ``builtin`` otherwise: a deterministic, coverage-guided mutational fuzzer on the standard library. Coverage is
  the set of (file, previous line, line) edges in the target's files, traced with sys.settrace; an input that
  reaches a new edge joins the corpus; mutations are byte flips, inserts, deletes, splices and the string
  literals of the target's own source (a dictionary, as libFuzzer's -dict). Seeded, so a run replays.
"""
import base64
import importlib.util
import json
import os
import random
import sys

#: Where the container mounts the target and the output (fixed in the sandbox; a test double may point them
#: at its own directories).
TARGET = os.environ.get("VR_TARGET", "/target")
OUT = os.environ.get("VR_OUT", "/out")


def _load_harness(path):
    spec = importlib.util.spec_from_file_location("vr_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _builtin(spec, harness):
    rnd = random.Random(int(spec.get("seed") or 0))
    target_prefix = os.path.realpath(TARGET) + "/"
    edges = set()
    cur = []

    def tracer(frame, event, arg):
        fn = frame.f_code.co_filename
        if not fn.startswith(target_prefix):
            return None
        if event == "line":
            edges_now.add((fn, cur[0] if cur else 0, frame.f_lineno))
            cur[:] = [frame.f_lineno]
        return tracer

    def run(text):
        global edges_now
        edges_now.clear()
        cur[:] = []
        sys.settrace(tracer)
        try:
            harness.TestOneInput(text)
            return None
        except BaseException as exc:  # noqa: BLE001 — a crash
            return exc
        finally:
            sys.settrace(None)

    corpus = [str(s) for s in (spec.get("seeds") or [""])] or [""]
    words = [str(w) for w in spec.get("dictionary") or [] if w]
    max_len = int(spec.get("max_input_len") or 256)
    budget = int(spec.get("iterations") or 2000)
    alphabet = [chr(c) for c in range(32, 127)]
    for i in range(budget + len(corpus)):
        if i < len(corpus):
            text = corpus[i]
        else:
            text = rnd.choice(corpus)
            for _ in range(rnd.randint(1, 4)):
                op = rnd.randrange(6)
                pos = rnd.randint(0, len(text))
                if op == 0 and text:
                    j = rnd.randrange(len(text))
                    text = text[:j] + chr((ord(text[j]) ^ (1 << rnd.randrange(7))) % 128) + text[j + 1:]
                elif op == 1:
                    text = text[:pos] + rnd.choice(alphabet) + text[pos:]
                elif op == 2 and text:
                    j = rnd.randrange(len(text))
                    text = text[:j] + text[j + 1:]
                elif op == 3 and words:
                    text = text[:pos] + rnd.choice(words) + text[pos:]
                elif op == 4:
                    other = rnd.choice(corpus)
                    text = text[:pos] + other[rnd.randint(0, len(other)):]
                else:
                    text = text[:pos] + rnd.choice("0123456789,=;:") + text[pos:]
            text = text[:max_len]
        exc = run(text)
        if exc is not None:
            return {"engine": "builtin", "crashed": True, "exception": type(exc).__name__,
                    "crash_input": base64.b64encode(text.encode("latin-1", "replace")).decode(),
                    "iterations": i + 1, "coverage": len(edges | edges_now), "corpus": len(corpus)}
        if not edges_now <= edges:
            edges.update(edges_now)
            if text not in corpus:
                corpus.append(text)
    return {"engine": "builtin", "crashed": False, "exception": None, "crash_input": None,
            "iterations": budget, "coverage": len(edges), "corpus": len(corpus)}


edges_now = set()


def _atheris(spec, harness, atheris):
    crash = {}

    def one(data):
        text = data.decode("latin-1")[: int(spec.get("max_input_len") or 256)]
        try:
            harness.TestOneInput(text)
        except BaseException as exc:  # noqa: BLE001
            crash.update(exception=type(exc).__name__,
                         crash_input=base64.b64encode(text.encode("latin-1", "replace")).decode())
            # libFuzzer ends the process on a crash: record it first.
            with open(os.path.join(OUT, spec["id"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump({"engine": "atheris", "crashed": True, "exception": crash["exception"],
                           "crash_input": crash["crash_input"], "iterations": None, "coverage": None,
                           "corpus": None}, fh)
            raise
    seeds_dir = "/tmp/vr_seeds"
    os.makedirs(seeds_dir, exist_ok=True)
    for i, s in enumerate(spec.get("seeds") or [""]):
        with open(os.path.join(seeds_dir, f"seed{i}"), "wb") as fh:
            fh.write(str(s).encode("latin-1", "replace"))
    dict_path = "/tmp/vr.dict"
    with open(dict_path, "w", encoding="latin-1") as fh:
        for w in spec.get("dictionary") or []:
            fh.write(json.dumps(str(w)) + "\n")
    argv = ["fuzz", f"-runs={int(spec.get('iterations') or 2000)}", f"-seed={int(spec.get('seed') or 1)}",
            f"-max_len={int(spec.get('max_input_len') or 256)}", f"-dict={dict_path}",
            "-artifact_prefix=/tmp/", seeds_dir]
    atheris.Setup(argv, one)
    try:
        atheris.Fuzz()
    except SystemExit:
        pass
    except BaseException:  # noqa: BLE001
        pass
    return {"engine": "atheris", "crashed": bool(crash), "exception": crash.get("exception"),
            "crash_input": crash.get("crash_input"),
            "iterations": None if crash else int(spec.get("iterations") or 2000),
            "coverage": None, "corpus": None}


def main(path):
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    sys.path.insert(0, os.path.join(TARGET, spec.get("src_root") or "."))
    try:
        import atheris  # noqa: F401  (only in the pinned fuzz image, deploy/vuln-sandbox)
    except ImportError:
        atheris = None
        print("fuzz_runner: atheris is not installed in this image; the built-in coverage-guided fuzzer runs",
              file=sys.stderr)
    if atheris is not None and spec.get("engine") != "builtin":
        with atheris.instrument_imports():
            harness = _load_harness(os.path.join(os.path.dirname(os.path.abspath(path)), spec["harness"]))
        out = None
        pid = os.fork()
        if pid == 0:   # libFuzzer exits the process on a crash: run it in a child, report from the parent
            res = _atheris(spec, harness, atheris)
            with open(os.path.join(OUT, spec["id"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump(res, fh)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        if not os.path.exists(os.path.join(OUT, spec["id"] + ".json")):
            # libFuzzer exits the process itself when it finishes its runs: exit 0 is "no crash".
            clean = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            out = {"engine": "atheris", "crashed": not clean, "exception": None if clean else "unknown",
                   "crash_input": None, "iterations": int(spec.get("iterations") or 2000) if clean else None,
                   "coverage": None, "corpus": None}
    else:
        harness = _load_harness(os.path.join(os.path.dirname(os.path.abspath(path)), spec["harness"]))
        out = _builtin(spec, harness)
    if out is not None:
        with open(os.path.join(OUT, spec["id"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh)


if __name__ == "__main__":
    main(sys.argv[1])
