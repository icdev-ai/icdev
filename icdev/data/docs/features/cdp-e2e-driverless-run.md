# CUI // SP-CTI

# Running the E2E estate driverless over CDP (cdp-wd-02)

**Card:** CDP Air-Gap Browser Automation (`cdp-`)
**Spike:** [docs/spikes/cdp-00-browser-automation-airgap-adaptation.md](../spikes/cdp-00-browser-automation-airgap-adaptation.md) §4.8

The `tests/e2e_selenium/` estate (30 modules) and the `tests/e2e_*.py` scripts can
run with **no WebDriver binary**, driving the browser over the Chrome DevTools
Protocol instead. This is the air-gap payoff: nothing to version-match, pre-stage,
or re-vendor when the browser auto-updates.

## The one lever

Every estate module builds its driver with `tools.browser.driver_manager.get_driver()`.
That function is **opt-in backend-aware** (cdp-wd-02): set one environment variable
and it returns the CDP `WebDriver` facade instead of a Selenium driver — **no test
edits**.

```bash
# Run the whole estate driverless (CDP), against a running dashboard.
ICDEV_BROWSER_BACKEND=cdp pytest tests/e2e_selenium/ -v
```

Unset (or `selenium`), `get_driver()` behaves exactly as before, so the default and
the 108 agent-browser tests are unaffected.

## Why this is near-zero-edit

The `selenium` **Python package** is pure Python and installs offline — only the
driver *binary* is unavailable air-gapped. So `By`, `WebDriverWait`, and
`expected_conditions` are plain helpers that just call `driver.find_element(...)`,
and they work **unchanged** against the duck-typed `CDPWebDriver`
(`tools/browser/cdp/webdriver.py`, cdp-wd-01). The estate uses `page.driver.find_element(By.TAG_NAME, "body").text`, `find_elements`, `click`, `send_keys`,
`execute_script`, `save_screenshot`, `current_url`, `title`, `page_source`,
`get_cookies`, `implicitly_wait`, `set_window_size` — all provided by the facade.

## Preconditions and how to check them

1. **A Chromium-family browser is installed** (Edge/Chrome/Chromium). Check:
   ```bash
   python tools/browser/browser_locator.py --all --json
   ```
2. **Remote debugging is permitted** (the `RemoteDebuggingAllowed` policy is unset
   or `1`). Check the tier this host will use:
   ```bash
   python tools/browser/cdp/preflight.py --json     # Tier 1 == CDP available
   python tools/browser/cdp/preflight.py --gate      # exit 1 if forced to Tier 3
   ```
3. **The dashboard is running** at `ICDEV_DASHBOARD_URL` (default
   `http://localhost:5050`). The estate's `conftest.py` skips every test when it is
   unreachable.

`vendor/drivers/` may be **empty** — that is the point. If it holds a stale driver,
`python tools/browser/driver_manager.py --check-staleness` reports it, but the CDP
backend does not use it at all.

## When CDP is not available

If preflight reports **Tier 3** (policy forbids debugging, or no browser), there is
no browser transport — see
[cdp-tier-ladder-and-tier3-degradation.md](cdp-tier-ladder-and-tier3-degradation.md)
for what Tier 3 still verifies (`route_smoke` / `api_contract_tester` /
`fathomdesk_smoke`) and what is lost (rendered-DOM checks). Do **not** set
`ICDEV_BROWSER_BACKEND=cdp` on such a host — the launch will refuse loudly rather
than silently pass.

# CUI // SP-CTI
