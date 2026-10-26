# ADR-0057 — The Android client themes Material3 with the MOSAIQ tokens, adapted for a handheld

**Status:** Proposed
**Owner:** Max
**Issue:** #717 (UX-01), epic #716
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0002](0002-mosaiq-identity-and-design-system.md) made MOSAIQ the product
identity and wrote its design system for one consumer: the server-rendered
Flask application. The Android floor app (ADR-0041, phase 17) shipped without
any of it. `Screens.kt` wraps the app in a bare `MaterialTheme {}`, the manifest
theme is `Theme.Material.Light.NoActionBar`, there is no `Color.kt`, `Type.kt`
or `Theme.kt`, and the launcher icon is a `#1F4E79` blue that is not a MOSAIQ
token. The result is a stock Material3 app, light-only, that looks nothing like
the web system the same people use.

Reusing the design system on Android is not a copy. ADR-0002 describes a dense
analytical tool: 28px compact controls, 13px type, and a 24px dense density,
read on a desktop for hours. The Android app is used standing up, one-handed,
in a shop, by floor staff. Three facts decide the mapping and none of them
is obvious from the CSS:

- **The accent fails as text.** `orange-600` (`#D45400`) on the canvas
  (`#FAFAF9`) is 3.97:1, under the 4.5:1 that body text needs. White on
  `orange-600` is 4.14:1, also under. Black on `orange-600` is 5.07:1, which is
  why the web system sets black on accent. Links and accent-colored text use
  `orange-700` (`#A33F00`, 6.15:1), as `semantic.css` already does.
- **The web hairline border is invisible on a phone in daylight.**
  `neutral-300` on the canvas is 1.41:1. For a text field the boundary is the
  affordance, and WCAG 1.4.11 asks for 3:1. `neutral-500` (`#8C8C86`) gives
  3.24:1 on the canvas and 3.38:1 on white.
- **Material3 has no success, warning or info roles.** MOSAIQ's state colors
  and service-health hues have nowhere to live in `ColorScheme`.

What is genuinely uncertain: the contrast figures above were computed for
this record from the token hex values, not read from `resolved-tokens.css`;
the icon set (below) is a judgement call; and no real device screenshot has
been compared to the web system yet. UX-17 does that.

## Decision

The Android client has one theme, `MosaiqTheme`, in `ui/theme/`, built by
mapping the MOSAIQ semantic tokens (`tokens/semantic.css`, light and the
`data-mq-theme="dark"` block) onto Material3's `ColorScheme`, `Typography` and
`Shapes`, with the roles Material3 lacks carried by a small `MosaiqColors`
object exposed through a `CompositionLocal`. The theme follows the system
light or dark setting, with no in-app switch, and turns dynamic color off so
the brand does not change with the wallpaper. The density is always the
comfortable one: controls and rows are at least 44dp, the compact and dense
densities of ADR-0002 are never used on Android, and every screen is checked at
375dp width. Hand-written color, type size, shape or spacing values are
allowed only inside `ui/theme/`.

The mapping, light first with dark in parentheses:

| Material3 role | MOSAIQ token | Value |
|---|---|---|
| `background` | `--mq-bg-canvas` | `#FAFAF9` (`#0B0B0A`) |
| `surface` | `--mq-bg-surface` | `#FFFFFF` (`#141412`) |
| `surfaceVariant` (sunken) | `--mq-bg-sunken` | `#F4F4F2` (`#232320`) |
| `onSurface` | `--mq-text-primary` | `#232320` (`#F4F4F2`) |
| `onSurfaceVariant` | `--mq-text-secondary` | `#6B6B65` (`#B3B3AE`) |
| `outlineVariant` (dividers) | `--mq-border-subtle` | `#E7E7E4` |
| `outline` (field and control borders) | `neutral-500` | `#8C8C86` |
| `primary` | `--mq-accent` | `#D45400` (`#FF6B00`) |
| `onPrimary` | `--mq-on-accent` | `#000000` |
| `primaryContainer` | `--mq-accent-subtle` | `#FFE8D1` (`#3A1D00`) |
| `onPrimaryContainer` | `--mq-on-accent-subtle` | `#5C2300` |
| `error` | `--mq-danger` | `#C0342B` (`#E8736A`) |
| `errorContainer` | `--mq-danger-subtle` | `#FAE4E2` |
| `MosaiqColors.success / warning / info` | `--mq-success / warning / info` | `#1F7A4C / #B26A00 / #1F5FA8` (`#4FBF85 / #E0A44A / #6BA6E8`) |
| `MosaiqColors.segment[0..7]` | categorical `cat-01..08` | Paul Tol muted (Tol light in dark) |

Accent-colored text and links use `orange-700`, never `orange-600`. A segment
or a status is never carried by color alone: it always has a text label.

Typography is IBM Plex Sans, with IBM Plex Mono for identifiers, bundled in
`res/font` from the files vendored in `docs/design-system/fonts/`, with
tabular figures (`tnum`) on every number. The scale is the web one, shifted up
for a handheld: body is 14/20, not 13/18, and 12/16 is for captions only.

| Material3 style | Size / line | Weight |
|---|---|---|
| `labelSmall` | 12/16 | 500 |
| `bodyMedium` | 14/20 | 400 |
| `labelLarge` | 14/20 | 600 |
| `bodyLarge`, `titleMedium` | 16/24 | 400, 600 |
| `titleLarge` | 20/28 | 600 |
| `headlineSmall` | 24/32 | 600 |
| `headlineMedium` | 32/40 | 700 |

Shapes are the web radii, 2, 4 and 6dp, with 6dp as the ceiling. Spacing is
the 4dp scale (`Spacing.kt`: 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64). There is
no tonal elevation (`surfaceTint` transparent); the only two elevations are
the popover and modal shadows of ADR-0002.

Icons are Lucide, as the design system README recommends, vendored as vector
drawables, with no icon-library dependency. The launcher icon is an adaptive
icon with a `neutral-950` background and a white "M" taken from
`web/static/favicon.svg`; the `#1F4E79` blue is removed. There is still no
MOSAIQ logo, and this record does not invent one.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep default Material3 | It is what ships today. It contradicts ADR-0002 (the interface follows the MOSAIQ system) and gives a floor associate two different-looking products to learn. |
| Material You dynamic color | The accent, and therefore the identity, would follow the user's wallpaper, and the contrast guarantees above could not be stated. |
| Generate the Android tokens from the CSS with a build tool (Style Dictionary or similar) | It adds a build step the web system deliberately does not have, and a toolchain to maintain for a few dozen values. Drift is caught by a test instead (Compliance). |
| Use the compact or dense density for more rows per screen | ADR-0002 already forbids the 24px dense density on mobile, and a 28px control is below any usable touch target. |
| Use `orange-600` for links and accent text | 3.97:1 on the canvas fails AA for body text. |
| A Material Symbols or Material Icons dependency | It brings a second visual language into an interface whose icon set the design system already names. The icons the app needs are few. |

## Consequences

**What this makes easy.** One place defines every color, size and shape, so a
screen story (UX-06 to UX-11) composes theme roles and the shared kit
(UX-04) and never picks a value. Dark mode and the contrast guarantees come
with the theme instead of being retrofitted per screen.

**What this makes hard.** The tokens now exist in two places, CSS and Kotlin,
and a change to a MOSAIQ token has to reach both; the drift test is the only
thing that notices. Bundling Plex adds font weight to the APK. The `MosaiqColors`
extension is ours to maintain, because Material3 will never grow
those roles in a shape that matches.

**What must now be true elsewhere.**
- UX-02 (#720) builds the theme and UX-04 (#722) the component kit to this
  mapping. Neither may add a color, size or shape that is not in this record
  without an ADR that supersedes it.
- `docs/design-system/README.md` lists the Android client as a second consumer.
  ADR-0002 is not edited; it still governs the web system, and this record
  extends the same system to Android.
- The fonts' license is noted next to the converted `.ttf` files in
  `res/font`.
- The launcher icon in `res/drawable` stops using `#1F4E79`.

## Compliance

1. A JVM unit test reads `docs/design-system/tokens/resolved-tokens.css` and
   fails when a `Color` constant in `ui/theme/Color.kt` differs from the token
   it claims to map. This is the drift check.
2. The same test computes the contrast of each foreground/background pair the
   theme defines (`onPrimary` on `primary`, `onSurface` on `surface`, `outline`
   on `background`, and so on) and fails below 4.5:1 for text and 3:1 for
   borders, in both schemes.
3. `bash clients/android/check-adr41.sh` gains a check that prints a line for
   every `Color(0x` literal, `sp` text size or hardcoded `dp` shape outside
   `ui/theme/`.
4. Reviewers verify each screen at 375dp in light and dark; UX-17 records it.
