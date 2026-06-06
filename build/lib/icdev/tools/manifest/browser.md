# Browser Automation (Selenium Driver Manager)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Browser Automation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Driver Manager | tools/browser/driver_manager.py | Singleton WebDriver factory — resolves vendored msedgedriver (Edge primary, Win11 pre-installed) or chromedriver fallback; no runtime downloads; `get_driver()` returns ready WebDriver | `--probe`, `--json`, `--smoke` | Resolved browser + driver path; Selenium WebDriver instance |
| Browser Package | tools/browser/__init__.py | Package init — re-exports `get_driver`, `DriverManager` | (import) | — |

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
