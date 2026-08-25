# Design QA

- source visual truth path: `/Users/inaminetetsuo/.codex/generated_images/01a00e9e-6534-7bf3-a89c-699b140130b3/exec-7274ed2d-d163-4f35-8b29-4d198cdc128d.png`
- implementation screenshot path: `/private/tmp/keiba-ui-win5-final.png`
- viewport crop path: `/private/tmp/keiba-ui-win5-viewport.png`
- combined comparison path: `/private/tmp/keiba-ui-win5-comparison.png`
- viewport: 1440 x 1024 CSS pixels
- source pixels: 1487 x 1058, normalized to 1440 x 1024
- implementation full-page pixels: 1425 x 1204 at device scale 1; browser viewport 1440 x 1024 with a 1425-pixel content width after the vertical scrollbar
- comparison normalization: implementation's top 1024 pixels normalized to 1440 x 1024; source normalized to 1440 x 1024
- state: synthetic audited prediction bundle, synthetic audited walk-forward report, and synthetic audited WIN5 shadow forecast

## Full-view comparison evidence

The implementation preserves the selected design's navy header, 270-pixel research-context rail, red official-ticket band, winner-focused probability area, ranking table, and muted shadow-research area. The optional WIN5 shadow panel is an intentional product extension below the original shadow area, so the validation strip moves below the initial 1024-pixel viewport when WIN5 is present. There is no horizontal overflow.

## Focused comparison evidence

The official-ticket, winner/ranking, shadow, and top of the WIN5 extension were readable at full resolution in the combined 2880 x 1024 comparison. The separate full-page implementation capture records all five WIN5 legs, the joint probability, independence statement, and validation strip. No finer crop was needed because there are no raster assets or fine icon details.

## Required fidelity surfaces

- Fonts and typography: system Japanese sans-serif follows the reference's neutral product typography; hierarchy, numeric emphasis, line height, and wrapping are consistent. Horse IDs are longer than mock horse numbers because the audited fixture exposes IDs rather than program numbers.
- Spacing and layout rhythm: major tracks and section ordering match. The first implementation overflowed vertically because every shadow strategy was a separate row; the revised matrix uses ticket counts as rows and strategies as columns and fits one viewport.
- Colors and visual tokens: navy header, green audit state, coral official candidate, blue probabilities, cool gray research surfaces, and subtle dividers match the reference's semantic palette.
- Image quality and asset fidelity: the selected design contains no product photography, illustrations, or custom raster assets. No placeholder image, custom SVG, emoji, or code-drawn decorative asset was substituted.
- Copy and content: unavailable race names, horse names, venue, distance, going, and rationale tags were intentionally omitted rather than invented. The official 100-yen candidate and every zero-yen shadow remain explicit.
- WIN5 extension: the panel is visually separated from the official ticket, says `購入しない・0円`, shows exactly five targets, and labels the joint probability as an independence-based research value.

## Comparison history

1. P2: the initial shadow section created a 1218-pixel page and changed the reference's one-screen density.
   - Fix: regrouped every saved portfolio into a generator-by-strategy matrix with ticket count rows.
   - Post-fix evidence: the revised browser capture reports a 1440 x 1024 document inside a 1440 x 1024 viewport with no console warnings or errors.
2. No P0/P1/P2 issue was introduced by the optional WIN5 extension.
   - Evidence: the 2880 x 1024 side-by-side comparison preserves all original major-region widths and ordering; the full-page capture shows the extension below the original content without horizontal overflow.

## Findings

No actionable P0, P1, or P2 differences remain. Differences in names, dates, probabilities, and available research context are expected consequences of rendering audited synthetic data instead of the mock's illustrative content. The additional vertical length is the expected conditional WIN5 state, not a replacement for an original design region.

## Primary interactions tested

- Initial same-origin state fetch succeeds.
- The reload control fetches the same immutable audited state without navigation or mutation.
- At 700 CSS pixels, the responsive layout has no horizontal overflow.
- The WIN5 panel renders exactly five legs, stays visible after reload, and remains zero-stake in the API state.
- No browser console warnings or errors were recorded.

## Follow-up polish

- P3: translate internal strategy identifiers when the domain layer defines stable Japanese labels.

final result: passed
