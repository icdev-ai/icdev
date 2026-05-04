# RTL-SDR + PySDR Air-Gap Compatibility — Python 3.14
<!-- CUI // SP-CTI -->

**Date:** 2026-04-30
**Task:** sgx-sigint-01
**Purpose:** Verify rtlsdr/PySDR wheel availability for Python 3.14 on Windows/Linux; assess GNU Radio headless CPU/RAM requirements; document file-replay fallback mode.

---

## 1. Executive Summary

| Component | Python 3.14 Status | Air-Gap Ready | Notes |
|---|---|---|---|
| `pyrtlsdr` 0.4.0 | **COMPATIBLE** | Yes | Pure-Python (ctypes); `py3-none-any` wheel |
| `pyrtlsdrlib` 0.4.0 | **COMPATIBLE** (caveat) | Yes (mirror needed) | Pre-built librtlsdr binaries; tagged 3.8–3.11 but ABI-neutral |
| `numpy` ≥ 2.4.4 | **COMPATIBLE** | Yes | Py3.14 wheels published March 29 2026 |
| `scipy` ≥ 1.15.x | **UNCERTAIN** | Partial | Py3.14 stable wheels not confirmed as of 2026-04-30; build-from-source may be required |
| GNU Radio 3.10.x | **INCOMPATIBLE** | No | Bundles Python 3.11 via RadioConda; does not run under system Python 3.14 |
| File-replay mode | **N/A** | Yes | Hardware-free; works on any Python ≥ 3.10 |

**Recommendation:** Target **Python 3.11 or 3.12** for the full SDR stack. Use Python 3.14 only for components that are demonstrably compatible (`pyrtlsdr`, `numpy`). Reserve GNU Radio for dedicated RadioConda environments.

---

## 2. pyrtlsdr

### Package Details
- **PyPI:** `pyrtlsdr` (latest: 0.4.0, released 2026-03-01)
- **Companion:** `pyrtlsdrlib` (bundles pre-built `librtlsdr` DLL/SO for Windows and Linux)
- **Wheel tag:** `py3-none-any` — pure Python, no C extension build step required

### Python 3.14 Compatibility
`pyrtlsdr` uses `ctypes` exclusively to call into the native `librtlsdr` shared library. There are **no compiled C extensions** in the package itself, so the `py3-none-any` wheel installs and runs on any CPython including 3.14 with no changes.

`pyrtlsdrlib` ships pre-compiled binaries (`rtlsdr.dll` / `librtlsdr.so`) bundled as package data. The package metadata lists classifiers for Python 3.8–3.11, but the binaries are OS-native and Python-version-agnostic — they will load correctly under Python 3.14 via `ctypes`.

### Air-Gap Installation
```bash
# Download wheels from PyPI on a connected machine, then transfer
pip download pyrtlsdr pyrtlsdrlib --dest ./offline-wheels/

# On the air-gapped host (Python 3.14 OK)
pip install --no-index --find-links=./offline-wheels pyrtlsdr pyrtlsdrlib
```

**Windows:** The `pyrtlsdrlib` wheel bundles `rtlsdr.dll`; no separate Zadig/WinUSB driver step is needed at install time (driver still required for live hardware).
**Linux:** `pyrtlsdrlib` bundles `librtlsdr.so`; alternatively, install `librtlsdr-dev` from the distro package mirror.

### Verification
```python
import rtlsdr
print(rtlsdr.__version__)  # confirms import; hardware not required for this check
```

---

## 3. PySDR

"PySDR" refers to two distinct projects:

| Project | URL | Role |
|---|---|---|
| PySDR Textbook | pysdr.org (777arc/PySDR) | Interactive guide; not a pip-installable package |
| pySDR (aa2il) | github.com/aa2il/pySDR | Full software receiver application (Qt-based) |

### Dependencies (Textbook examples — the common usage)
PySDR examples depend on: `numpy`, `scipy`, `matplotlib`.

- **numpy ≥ 2.4.4**: Python 3.14 wheels available on PyPI as of 2026-03-29. **Compatible.**
- **scipy**: Python 3.14 wheel availability **not confirmed** as of 2026-04-30. The SciPy toolchain roadmap requires Python 3.11–3.14 for scipy ≥ 1.15.x, but binary wheels for 3.14 may require building from source or using an older Python.
- **matplotlib**: Generally follows numpy compatibility; test with `py3-none-any` wheels.

### Air-Gap Mitigation for scipy on Python 3.14
Option A — **Downgrade Python**: Use Python 3.11 or 3.12 where scipy binary wheels exist.
Option B — **Build wheel offline**: Pre-build scipy for 3.14 on a compatible machine and include the `.whl` in the offline cache.
Option C — **Conda-forge**: Use Miniforge (offline bundle) which may have conda-packaged scipy for 3.14 before PyPI wheels land.

---

## 4. GNU Radio Headless

### Version
GNU Radio **3.10.12.0** (released 2025-02-20) is the current stable release for Windows.

### Python Binding
GNU Radio embeds its own Python environment via **RadioConda** (Conda-based installer). The bundled Python is **3.11**. There is no supported path to run GNU Radio 3.10.x against a system Python 3.14 — the C++ binding layer (PyBind11 + gr-python) must be compiled against the exact Python ABI.

**Implication:** In air-gap deployments, ship the entire **RadioConda offline installer** (≈ 1.5 GB) rather than individual packages. Do not attempt to mix system Python 3.14 with GNU Radio.

### CPU and RAM Requirements (Headless / No GPU)

GNU Radio does not require a GPU. All DSP blocks run on CPU.

| Tier | CPU | RAM | Sample Rate Ceiling | Use Case |
|---|---|---|---|---|
| Minimum | Dual-core 2.2 GHz (x86_64) | 2 GB | ~2 MSPS | Simple demodulation (NBFM, AM) |
| Recommended | Quad-core 3.0 GHz+ | 4 GB | ~10 MSPS | Multi-channel decode, spectrum analysis |
| Production | 8-core 3.5 GHz+ | 8 GB | ≥ 20 MSPS | Wideband recording, simultaneous decoders |

**Headless-specific notes:**
- No display server required; disable all GUI sink blocks (`Qt GUI Sink`, `WX GUI`) — replace with `File Sink` or `ZMQ PUB Sink`.
- Set `GR_DONT_LOAD_PREFS=1` to skip preference file I/O in restricted environments.
- CPU usage scales linearly with sample rate and number of active blocks; RTL-SDR devices typically operate at 2.0–2.4 MSPS without overruns on a modern quad-core.

---

## 5. File-Replay Mode (Hardware Fallback)

When RTL-SDR hardware is unavailable (air-gap CI, unit tests, offline analysis), IQ file replay substitutes for live hardware. Three approaches ranked by complexity:

### 5.1 Pure-Python Replay (Recommended — no GNU Radio dependency)

```python
import numpy as np

def load_rtlsdr_iq(path: str, dtype=np.complex64) -> np.ndarray:
    """Load raw RTL-SDR 8-bit IQ file (.bin) as complex64 samples."""
    raw = np.fromfile(path, dtype=np.uint8)
    # RTL-SDR format: interleaved I, Q as unsigned 8-bit (offset 127.5)
    iq = (raw.astype(np.float32) - 127.5) / 127.5
    return (iq[0::2] + 1j * iq[1::2]).astype(np.complex64)

samples = load_rtlsdr_iq("recording.bin")
```

Depends only on `numpy` — compatible with Python 3.14, air-gap safe.

### 5.2 SigMF Format (Structured Replay)

SigMF (Signal Metadata Format) stores IQ data + JSON metadata in a standard pair:
- `recording.sigmf-data` — raw binary IQ
- `recording.sigmf-meta` — JSON: center frequency, sample rate, hardware, annotations

```bash
pip install sigmf          # py3-none-any; Python 3.14 compatible
```

```python
import sigmf
from sigmf import SigMFFile

with open("recording.sigmf-meta") as f:
    sm = SigMFFile(metadata=f.read(), data_file="recording.sigmf-data")

samples = sm.read_samples()   # returns numpy complex64 array
print(sm.get_global_field(SigMFFile.SAMPLE_RATE_KEY))   # e.g. 2048000
```

### 5.3 GNU Radio File Source Block

In a headless flowgraph (Python API, no GUI):

```python
import gnuradio
from gnuradio import gr, blocks

class ReplayFlowgraph(gr.top_block):
    def __init__(self, iq_file: str, sample_rate: int = 2048000):
        super().__init__()
        src = blocks.file_source(gr.sizeof_gr_complex, iq_file, repeat=False)
        throttle = blocks.throttle(gr.sizeof_gr_complex, sample_rate)
        snk = blocks.null_sink(gr.sizeof_gr_complex)
        self.connect(src, throttle, snk)

tb = ReplayFlowgraph("recording.cfile")
tb.run()
```

Requires RadioConda GNU Radio environment (Python 3.11).

### 5.4 rtl_tcp_echo (Transparent Proxy Replay)

`rtl_tcp_echo` is a Go binary that acts as a fake `rtl_tcp` server, serving pre-recorded IQ data to any rtl_tcp-compatible client (SDR#, CubicSDR, pyrtlsdr's `RtlSdrTcpClient`).

```bash
# Record
rtl_tcp_echo -record -out recording.iq -port 1234
# Replay (no hardware needed)
rtl_tcp_echo -replay -in recording.iq -port 1234
```

Useful for integration testing with `pyrtlsdr` client code unchanged:
```python
from rtlsdr import RtlSdrTcpClient
sdr = RtlSdrTcpClient(hostname='localhost', port=1234)
```

---

## 6. Recommended Stack for Air-Gap Deployment

```
┌─────────────────────────────────────────────────┐
│  Air-Gap SDR Stack                              │
├──────────────────┬──────────────────────────────┤
│ Python 3.11      │ GNU Radio (RadioConda)        │
│                  │ pyrtlsdr + pyrtlsdrlib        │
│                  │ numpy, scipy (binary wheels)  │
│                  │ sigmf                         │
├──────────────────┼──────────────────────────────┤
│ Python 3.14      │ pyrtlsdr + pyrtlsdrlib only   │
│                  │ numpy ≥ 2.4.4                 │
│                  │ sigmf                         │
│                  │ scipy: build-from-source      │
│                  │ GNU Radio: NOT SUPPORTED      │
└──────────────────┴──────────────────────────────┘
```

### Offline Package Manifest

```bash
# Collect all wheels on a connected machine (Python 3.11 target)
pip download \
  pyrtlsdr==0.4.0 \
  pyrtlsdrlib==0.4.0 \
  numpy \
  scipy \
  matplotlib \
  sigmf \
  --platform win_amd64 \
  --python-version 311 \
  --only-binary=:all: \
  --dest ./offline-wheels/

# For Linux (manylinux)
pip download \
  pyrtlsdr==0.4.0 pyrtlsdrlib==0.4.0 numpy scipy matplotlib sigmf \
  --platform manylinux2014_x86_64 \
  --python-version 311 \
  --only-binary=:all: \
  --dest ./offline-wheels-linux/
```

---

## 7. Open Risks

| Risk | Severity | Mitigation |
|---|---|---|
| scipy Python 3.14 wheels not available | Medium | Pin Python to 3.11/3.12 for scipy-dependent code |
| librtlsdr ABI break with future OS update | Low | Pin `pyrtlsdrlib` version; test on target OS |
| GNU Radio 3.10 end-of-life (upstream) | Medium | Watch for GNU Radio 3.11 release; evaluate SoapySDR as alternative |
| RTL-SDR USB driver missing on fresh Windows | Medium | Bundle Zadig + WinUSB preset in deployment package |
| rtl_tcp_echo Go binary not in PyPI mirror | Low | Pre-compile and bundle binary in `tools/sigint/` |

---

## 8. Sources

- [pyrtlsdr on PyPI](https://pypi.org/project/pyrtlsdr)
- [pyrtlsdrlib on PyPI](https://pypi.org/project/pyrtlsdrlib/)
- [pyrtlsdr GitHub README](https://github.com/pyrtlsdr/pyrtlsdr/blob/master/README.md)
- [PySDR Textbook — IQ Files and SigMF](https://pysdr.org/content/iq_files.html)
- [PySDR Textbook — RTL-SDR in Python](https://pysdr.org/content/rtlsdr.html)
- [GNU Radio Hardware Wiki](https://wiki.gnuradio.org/index.php/Hardware)
- [GNU Radio Hardware Considerations Tutorial](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Hardware_Considerations)
- [GNU Radio Windows Install](https://wiki.gnuradio.org/index.php/WindowsInstall)
- [rtl_tcp_echo — RTL-SDR Blog](https://www.rtl-sdr.com/rtl_tcp_echo-record-and-replay-iq-streams-with-a-transparent-rtl_tcp-proxy/)
- [Playing Back IQ Files with GNU Radio](https://www.site2241.net/june2022.htm)
- [NumPy on PyPI](https://pypi.org/project/numpy/)
- [SciPy Toolchain Roadmap](https://docs.scipy.org/doc/scipy/dev/toolchain.html)
- [Python 3.14 Readiness — pyreadiness.org](http://pyreadiness.org/3.14/)
