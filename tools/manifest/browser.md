# Browser Automation (Selenium Driver Manager)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Browser Automation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Driver Manager | tools/browser/driver_manager.py | Singleton WebDriver factory — resolves vendored msedgedriver (Edge primary, Win11 pre-installed) or chromedriver fallback; no runtime downloads; `get_driver()` returns ready WebDriver | `--probe`, `--json`, `--smoke` | Resolved browser + driver path; Selenium WebDriver instance |
| Agent Scope Controls | tools/browser/scope.py | oss-browse-02. Mandatory guardrail seam between an LLM-driven agent and a live WebDriver. `check_navigation(url)` enforces a default-deny domain allowlist (loopback only) + scheme allowlist + `egress_guard` for routable hosts; `SensitiveDataResolver` substitutes `<secret>name</secret>` at the driver (broker-authorized, env-sourced, never in prompt/transcript/audit); `ActionBudget` caps actions/failures/step wall-clock; `GuardedDriver` binds all of it to a session and writes one `audit_trail` row per action. Config: `args/browser_scope.yaml` (`ICDEV_BROWSER_SCOPE_CONFIG` overrides). | `--show`, `--check-url <url>`, `--json` | `ScopeDecision` / policy JSON; exit 1 when a URL is denied |
| Browser Package | tools/browser/__init__.py | Package init — re-exports `get_driver`, `DriverManager`, `GuardedDriver`, `check_navigation`, `load_scope_config` and the scope exception types | (import) | — |

### Driver resolution order
1. Vendored `vendor/drivers/msedgedriver/{major}/msedgedriver[.exe]` matching installed Edge major
2. `msedgedriver` on PATH (system-installed)
3. Vendored `vendor/drivers/chromedriver/{major}/chromedriver[.exe]`
4. `chromedriver` on PATH
5. Selenium Manager (bundled with selenium >= 4.6)

### Quick start

```python
from tools.browser.driver_manager import get_driver

driver = get_driver()          # headless Edge or Chrome
driver.get("http://localhost:5050")
driver.quit()
```

```bash
# Probe driver resolution
python tools/browser/driver_manager.py --probe

# Smoke test (launches browser, visits about:blank, quits)
python tools/browser/driver_manager.py --smoke
```
