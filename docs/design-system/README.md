# MOSAIQ design system

The interface design system decided in [ADR-0002](../adr/0002-mosaiq-identity-and-design-system.md).
Plain HTML and CSS with custom properties — no React, Tailwind, or build step.
The implementation target is Flask + Jinja2 server-rendered templates: every
component below is semantic HTML with `mq-`-prefixed BEM classes, meant to be
copied straight into a template.

MOSAIQ is a dense analytical tool (commercial analysts, store managers,
marketing staff, inventory planners, auditors reading tables of 200-800 rows),
not a consumer app: neutral chrome, saturated color reserved for data, 1px
borders over shadows, tabular figures everywhere.

## Files

| File | Contents |
|---|---|
| `styles.css` | Entry point. `@import`s everything below in the right order. |
| `tokens/fonts.css` | Google Fonts substitutions — IBM Plex Sans (UI), IBM Plex Mono (ids, timestamps, tabular overrides). No font files were supplied; swap this one file if the team gets real families. |
| `tokens/primitives.css` | Tier 1 — neutral ramp, the papaya accent, semantic hues, service-health hues, the three data palettes (categorical/sequential/diverging), type scale, spacing, radius, elevation, motion. |
| `tokens/semantic.css` | Tier 2 — semantic aliases, plus the entire dark theme as one `[data-mq-theme="dark"]` override block. |
| `tokens/component.css` | Tier 3 — component-level tokens and the three density classes (comfortable/compact/dense). |
| `tokens/resolved-tokens.css` | Generated reference only — every token's resolved value and contrast ratio in one place. Not loaded by any page. |
| `base.css` | Element defaults, type and tabular-number utilities. |
| `components.css` | Every component's CSS. BEM, `mq-` prefixed, references tier 2/3 tokens only — never a primitive directly. |
| `charts/mosaiq-highcharts-theme.js` | `Highcharts.setOptions` theme reading every value from the tokens at runtime, plus `MOSAIQCharts.categorical()/.sequential()/.diverging()` helpers. **Not wired into the app** — see ADR-0002 Consequences. |
| `charts/index.html` | Worked chart examples (stacked area, RFM heatmap, migration sankey, uplift with CI) against the theme above, loading Highcharts from a CDN. |
| `guidelines/foundations-sheet.html` | Deliverable 1 — ramps, palettes with their colour-vision-deficiency notes, type scale, spacing, density, focus ring, radius/elevation, all on one page. |
| `guidelines/component-sheet.html` | Deliverable 2 — every component and state as plain semantic HTML. **This is the page to copy from into Jinja2 templates.** |

Open either guidelines page directly in a browser (`docs/design-system/guidelines/foundations-sheet.html`) to see the system rendered.

## What is not in this folder

The generated package also included 26 React component wrappers
(`components/*.jsx`), two React UI kits (`ui_kits/mosaiq-app`,
`ui_kits/mosaiq-site`), and a marketing/landing-page surface
(`marketing.css`, brand mosaics). None of that is committed:

- **React is off-scope.** The repository is one server-rendered Flask +
  Jinja2 application (`AGENTS.md`, C-2/C-3); the JSX files existed only as
  Claude Design's own preview tooling and produce the same markup as
  `components.css` — nothing is lost by working from
  `guidelines/component-sheet.html` instead.
- **No marketing site is in this delivery's scope** (`docs/scope.md` §1). If
  one is added later, `marketing.css` and the mosaic imagery can be pulled
  back from the original export.

## Known gaps (open with the team)

- No logo or icon set exists. Icons today are Unicode glyphs in IBM Plex
  Mono; **Lucide** (CDN, `unpkg.com/lucide-static/`) is the recommended
  substitution if the team wants real icons.
- No font files were supplied — `tokens/fonts.css` pulls IBM Plex from Google
  Fonts as a placeholder.
- The Service Health Board concept (from the original design brief) and the
  Highcharts theme's use of embedded chart data are flagged against this
  repository's constraints — see ADR-0002 Consequences before building
  either.
