# Design QA

- Source visual truth: `/Users/inaminetetsuo/.codex/generated_images/01a00e9e-6534-7bf3-a89c-699b140130b3/exec-061a1eae-6468-4cc6-ae51-455bff02e198.png`
- Implementation screenshot: `/tmp/keiba-ui-implementation-1488x1060.png`
- Combined comparison: `/tmp/keiba-ui-comparison.png`
- Previous overview screenshot: `/private/tmp/keiba-race-day-dashboard-final.png`
- Previous detail screenshot: `/private/tmp/keiba-race-detail-no-scroll.png`
- Source pixels: 1487 x 1058
- Implementation pixels: 1488 x 1060
- CSS viewport: 1488 x 1060 at browser density 1
- State: three venues, selected venue with 12 races, WIN5 shadow visible

## Full-view comparison evidence

The implementation preserves the selected design's navy header, date and venue controls, dense 12-race ledger, blue probability emphasis, red trifecta treatment, and bottom WIN5 research panel. The earlier top-right first-race ticket is intentionally absent because the user subsequently removed it from the overview requirements. The Japanese product name is retained from the existing application rather than reverting to the English mock label.

## Focused region comparison evidence

The dense race ledger and WIN5 panel were checked separately because they contain the smallest text and repeated alignment. Confirmed runner metadata uses the same frame colors, horse numbers, and horse names in the overview, detail winner card, ranking table, official trifecta, and WIN5 legs. Missing metadata falls back to horse IDs without inventing a frame color.

## Findings

No actionable P0, P1, or P2 differences remain.

- P3: The source includes a small calendar icon beside the date. The implementation omits the decorative icon and keeps the date as read-only text; this does not change the available interaction.
- P3: Synthetic QA data repeats probabilities and horse-name stems. This is fixture content, not a production layout constraint.

## Required fidelity surfaces

- Fonts and typography: existing macOS Japanese system stack retained; hierarchy, weights, truncation, and numeric alignment remain consistent with the selected design.
- Spacing and layout rhythm: 1488 x 1060 and 1440 x 900 fit without overflow. A compact desktop rule also fits 12 races and WIN5 at 1280 x 800 without horizontal or vertical overflow.
- Colors and visual tokens: existing navy, blue probability, red official-ticket, green audit, and eight JRA frame colors are preserved.
- Image quality and assets: the screen contains no photographic or illustrative assets. No placeholder raster or generated image is required.
- Copy and content: purchase candidate, zero-yen shadow status, WIN5 research status, and non-guarantee notice remain explicit.

## Comparison history

1. Previous baseline P0: the first browser render had a 1980-pixel document because the detail grid overrode the native `hidden` behavior.
   - Fix: added a global `[hidden] { display: none !important; }` rule.
   - Post-fix evidence: the dashboard and detail each reported a 1440 x 1024 document within a 1440 x 1024 viewport.
2. Previous baseline P2: the first implementation used predicted rank marker `1`, which resembled a horse number before runner display metadata was approved.
   - Fix: changed the fallback marker to the explicit label `1位`.
   - Post-fix evidence: missing runner display metadata remains visibly distinct from an audited horse number.
3. Current refinement P2: the initial compact pass at 1280 x 800 exceeded the viewport by 27 pixels.
   - Fix: reduced only the desktop toolbar and ledger row heights at short viewports, retaining every field and all 12 races.
   - Post-fix evidence: 1280 x 800 reports document height 800, body height 800, no horizontal overflow, 12 race rows, three venue tabs, and five WIN5 legs.
4. Current interaction evidence: venue tabs switch with click and arrow keys; a race opens the audited detail; the detail shows the same horse number, name, frame color, and trifecta numbers; returning restores all 12 rows; browser console reported no warnings or errors from the application.

## Implementation checklist

- [x] Keep overview, detail, trifecta, and WIN5 runner display consistent.
- [x] Preserve safe horse-ID fallback when display metadata is absent.
- [x] Fit 12 races and WIN5 at desktop heights down to 800 pixels.
- [x] Preserve every manifest venue and support keyboard tab navigation.
- [x] Verify overview-to-detail-to-overview interaction and console output.
- [x] Preserve reload behavior and keep WIN5 out of the race-detail screen.

final result: passed
