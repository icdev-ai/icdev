# E2E Test: NSA ZIG (Zero Trust Implementation Guide) Pages

Verify the NSA ZIG canvas pages under /security/zig/* render their core widgets
(radar chart, phase progress, FY2027 readiness bar) and that each page injects
exactly ONE IQE query widget (`.iqe-widget`, provided once via
`security_canvas/base.html`).

## Prerequisites
- Flask dashboard running on http://localhost:5050
- Server started with `ICDEV_AUTH_BYPASS=1` (all /security/zig/* routes are auth-guarded)
- ZIG constants seeded (7 pillars / 42 capabilities / 91 activities)

## Steps

1. Navigate to http://localhost:5050/security/zig/
2. Wait for the ZIG dashboard to load
3. Assert the hero heading "NSA Zero Trust Implementation Guide" is present
4. Assert the radar chart canvas `#zigRadar` is present
5. Assert the phase progress grid `.phase-grid` renders `.phase-box` cards with `.phase-pct` values
6. Assert the FY2027 readiness bar `.fy-bar-container` renders its `.fy-bar-fill` and `.fy-bar-label`
7. Assert exactly ONE `.iqe-widget` is present
8. Screenshot the ZIG dashboard

9. Navigate to http://localhost:5050/security/zig/pillar/user
10. Wait for the pillar detail page to load
11. Assert the pillar hero `.pillar-hero` and a "Pillar" heading are present
12. Assert capability cards `.cap-card` are displayed
13. Assert exactly ONE `.iqe-widget` is present
14. Screenshot the pillar detail page

15. Navigate to http://localhost:5050/security/zig/phase
16. Wait for the phase tracker page to load
17. Assert the "ZIG Phase Tracker" heading and `.phase-header` with `.phase-pct-ring` are present
18. Assert exactly ONE `.iqe-widget` is present
19. Screenshot the phase tracker page

20. Navigate to http://localhost:5050/security/zig/assessment
21. Wait for the assessment page to load
22. Assert the "ZIG Gap Assessment" heading, the `#runAssessBtn` button, and `.assess-card` are present
23. Assert exactly ONE `.iqe-widget` is present
24. Screenshot the assessment page

25. Navigate to http://localhost:5050/security/zig/roadmap
26. Wait for the roadmap page to load
27. Assert the "ZIG Compliance Roadmap" heading, the `.stat-grid`, and the `.timeline` with `.milestone` markers are present
28. Assert exactly ONE `.iqe-widget` is present
29. Screenshot the roadmap page

30. Navigate to http://localhost:5050/security/zig/portfolio
31. Wait for the portfolio page to load
32. Assert the "ZIG Portfolio" heading, the `.health-panel` with `.kpi-row`, and the compare radar canvas `#portRadar` are present
33. Assert exactly ONE `.iqe-widget` is present
34. Screenshot the portfolio page

## Expected Results
- ZIG dashboard shows the radar chart, phase progress grid, and FY2027 readiness bar
- Pillar detail (`user`) shows the pillar hero and capability cards
- Phase, assessment, roadmap, and portfolio pages each render their signature widgets
- Every /security/zig/* page injects exactly ONE `.iqe-widget`

## IQE Verification
- Each visited page must contain exactly one element matching `.iqe-widget`
  (injected once by `security_canvas/base.html`; the orphan
  `zig/external_canvas.html` template was removed).
