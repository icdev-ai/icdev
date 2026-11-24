# CUI // SP-CTI
"""Replay ONE input against one entry point and judge its oracle (icx-ent-01). Runs inside the sandbox only.

Usage (in the container): python /harness/pov_runner.py /harness/<spec>.json
Prints one JSON line: {"fired": bool, "decisive": bool, "oracle": ..., "exception": ..., "detail": ...}.

Standard library only. The oracles are canary-only:
  file_created  the canary file under /tmp exists after the call (it is removed first);
  opened        the call opened the planted canary file (an audit hook records every open);
  returned      the canary token appears in the call's return value;
  exception     the call raised an exception that is not one the entry point may raise to reject input.
"""
import base64
import importlib
import json
import os
import signal
import sys

#: Where the container mounts the target and the output (fixed in the sandbox; a test double may point them
#: at its own directories).
TARGET = os.environ.get("VR_TARGET", "/target")
OUT = os.environ.get("VR_OUT", "/out")


def _input(spec):
    raw = spec["input"]
    if spec.get("input_encoding") == "base64":
        return base64.b64decode(raw).decode("latin-1")
    return raw


def main(path):
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    canary = spec["canary"]
    try:
        os.remove(canary)
    except OSError:
        pass
    if spec.get("plant"):
        with open(canary, "w", encoding="utf-8") as fh:
            fh.write(spec["token"])
    opened = []
    real_canary = os.path.realpath(canary)

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes)):
            try:
                p = args[0].decode() if isinstance(args[0], bytes) else args[0]
                if os.path.realpath(p) == real_canary:
                    opened.append(p)
            except Exception:  # noqa: BLE001
                pass

    sys.path.insert(0, os.path.join(TARGET, spec.get("src_root") or "."))
    out = {"fired": False, "decisive": True, "oracle": spec["oracle"], "exception": None, "detail": ""}
    try:
        mod = importlib.import_module(spec["module"])
        fn = getattr(mod, spec["function"])
    except Exception as exc:  # noqa: BLE001
        out.update(decisive=False, detail=f"could not import the entry point: {type(exc).__name__}: {exc}")
        print(json.dumps(out))
        return
    sys.addaudithook(hook)
    expected = tuple(getattr(__builtins__, n, None) if not isinstance(__builtins__, dict) else __builtins__.get(n)
                     for n in spec.get("expected_exceptions") or [])
    expected = tuple(e for e in expected if isinstance(e, type))

    def _alarm(signum, frame):
        raise TimeoutError("call timeout")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(int(spec.get("call_timeout") or 5))
    result = None
    try:
        result = fn(_input(spec))
    except TimeoutError:
        out.update(decisive=False, detail="the call did not return in time")
    except BaseException as exc:  # noqa: BLE001 — judging what escaped is the point
        out["exception"] = type(exc).__name__
        if spec["oracle"] == "exception" and not isinstance(exc, expected):
            out["fired"] = True
    finally:
        signal.alarm(0)
    oracle = spec["oracle"]
    if oracle == "file_created":
        out["fired"] = os.path.exists(canary) and not spec.get("plant")
    elif oracle == "opened":
        out["fired"] = bool(opened)
    elif oracle == "returned":
        try:
            out["fired"] = spec["token"] in repr(result)
        except Exception:  # noqa: BLE001
            out["fired"] = False
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1])
