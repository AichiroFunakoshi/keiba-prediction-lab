# Design QA

- historical source visual filename: `exec-061a1eae-6468-4cc6-ae51-455bff02e198.png`
- historical implementation screenshot: `keiba-race-day-dashboard-final.png`
- historical detail screenshot: `keiba-race-detail-no-scroll.png`
- historical combined comparison: `keiba-dashboard-comparison-final.png`
- viewport: 1440 x 1024 CSS pixels at device scale 1
- source pixels: 1487 x 1058, normalized to 1440 x 1024
- implementation pixels: 1440 x 1024
- state: twelve synthetic audited prediction bundles, one race-day manifest, an audited five-leg WIN5 shadow, and an audited walk-forward report

The original captures were local QA artifacts and are not part of this repository. Their former absolute Mac and temporary-directory paths were intentionally removed because they were not portable evidence. Re-running visual QA must create a new dated evidence set and record either repository-relative files that are safe to publish or hashes for deliberately local-only files.

## Full-view comparison evidence

The 2880 x 1024 side-by-side comparison preserves the selected design's navy header, date and venue controls, coral featured trifecta, six-column twelve-row ledger, and bottom WIN5 research strip. Both source and implementation fit the same 1440 x 1024 viewport without horizontal or vertical scrolling.

## Focused comparison evidence

No additional crop was required. At original comparison resolution, row typography, dividers, trifecta tokens, detail affordances, all five WIN5 legs, and the joint probability remain legible. The separate 1440 x 1024 detail capture verifies that the destination screen also fits without scrolling.

## Required fidelity surfaces

- Fonts and typography: the implementation uses the established Japanese system-sans stack, matching the reference's neutral research-product tone and compact table hierarchy. The top brand remains the product's Japanese name rather than the generated English concept label.
- Spacing and layout rhythm: toolbar, twelve race rows, WIN5 strip, and disclaimer occupy the same vertical sequence and density. Major columns align across the header and rows.
- Colors and visual tokens: navy header and selection states, green audit badge, coral official tickets, blue probabilities, cool-gray headers, and subtle blue-gray rules match the reference semantics.
- Image quality and asset fidelity: the selected design contains no raster imagery, illustration, logo asset, or non-standard icon requiring generation. No placeholder image, custom SVG, emoji, gradient, or decorative drawing was introduced.
- Copy and content: unavailable horse names and program numbers are not invented. Audited horse IDs are displayed instead, and the small navy marker says `1位` so it cannot be mistaken for a program number. Purchase-free WIN5 language and the result disclaimer remain explicit.

## Comparison history

1. P0: the first browser render had a 1980-pixel document because the detail grid overrode the native `hidden` behavior.
   - Fix: added a global `[hidden] { display: none !important; }` rule.
   - Post-fix evidence: the dashboard and detail each report a 1440 x 1024 document within a 1440 x 1024 viewport.
2. P2: the first implementation used the predicted rank marker `1`, which visually resembled the reference's horse program number.
   - Fix: changed the marker to the explicit label `1位` because the audited artifact does not contain program numbers.
   - Post-fix evidence: the final comparison clearly distinguishes rank from the adjacent horse ID.

## Findings

No actionable P0, P1, or P2 differences remain. Horse names, program-number colors, dates, probabilities, and the number of visible venue tabs differ because the browser fixture contains one venue and exposes audited horse IDs only. These are evidence constraints rather than design drift.

## Primary interactions tested

- Initial same-origin state fetch succeeds and renders exactly twelve race rows.
- A race row opens the matching audited detail view.
- `全レースへ戻る` restores the overview.
- WIN5 appears on the overview and is absent from the detail view.
- Reload preserves the immutable audited state.
- Dashboard and detail have no horizontal or vertical overflow at 1440 x 1024.
- No browser console warnings or errors were recorded.

## Follow-up polish

- P3: when the approved input contract gains program numbers and horse names, display those audited values without replacing horse IDs in the underlying state.

final result: passed
