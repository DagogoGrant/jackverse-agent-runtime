# JackVerse Caseworker Design V2 Specification
**The Living Editorial System: Paper as Primary Identity, Ink as Alternate**

---

## 1. Vision & Core Philosophy

JackVerse Caseworker V2 is an authored **Living Editorial System**. It moves decisively beyond conventional SaaS dashboards, flat dark-mode minimalism, and generic "AI assistant" tropes. The interface is engineered as an authoritative publication—tactile, typographical, physically grounded, and kinetic.

### The Three Foundational Pillars
1. **Editorial**: The layout treats state as published content. Information is presented through broadsheets, dossiers, classified catalogues, and chronological chronicles. Hierarchy is established through stark typographical scale, generous margins, disciplined 1px hairlines, and asymmetric balance.
2. **Kinetic**: Motion in JackVerse is structural rather than decorative. It conveys state transitions through native View Transitions, progressive morphing of status badges, rolling metric tallies, tactile physical button presses (`scale(0.97)`), and intentional drag-to-authorize governance gates.
3. **Alive**: JackVerse breathes with restrained ambient awareness. An ultra-subtle 2D coordinate grid tracks system liveness without distraction, pausing entirely under reduced-motion preferences, document blur, or resting idle state.

### The Ratio: 70% Human Language / 30% Editorial Machine Texture
- **70% Human Voice**: Action titles, constitutional boundaries, mission objectives, and operational outcomes are articulated in precise, dignified human language.
- **30% Machine Texture**: Monospaced cryptographic hashes, ETag precondition tags, monotonic stream cursors, and state machine versioning provide genuine structural veracity without superficial sci-fi clutter.

---

## 2. Dual Identity Token Architecture

JackVerse Caseworker implements an authored dual-identity system built on CSS custom properties (`web/src/styles/tokens.css`) mapped seamlessly into Tailwind CSS (`web/tailwind.config.ts`).

### The Two Identities
- **Paper Identity (Default / Primary)**:
  - Evokes archival heavy paper stock, morning broadsheets, and typed intelligence dossiers.
  - Surface: `--jv-bg: #F2F0E9`, Panel: `--jv-surface: #F7F5EF`, Sunken: `--jv-surface-sunken: #E7E4DC`
  - Ink: `--jv-ink: #0A0A09`, Soft: `--jv-ink-soft: #34332F`, Muted: `--jv-muted: #79766E`
  - Rules: `--jv-rule: rgba(10, 10, 9, 0.14)`, Strong Rule: `--jv-rule-strong: rgba(10, 10, 9, 0.32)`
- **Ink Identity (Alternate / Darkroom)**:
  - Evokes an optical darkroom, photographic contact sheets, and nocturnal surveillance.
  - Surface: `--jv-bg: #050505`, Panel: `--jv-surface: #0A0A09`, Sunken: `--jv-surface-sunken: #000000`
  - Ink: `--jv-ink: #F2F0E9`, Soft: `--jv-ink-soft: #C5C1B8`, Muted: `--jv-muted: #77736C`
  - Rules: `--jv-rule: rgba(242, 240, 233, 0.14)`, Strong Rule: `--jv-rule-strong: rgba(242, 240, 233, 0.32)`

### Semantic Token Table

| Token Variable | Paper Identity | Ink Identity | Functional Role |
| :--- | :--- | :--- | :--- |
| `--jv-bg` | `#F2F0E9` | `#050505` | Primary canvas surface |
| `--jv-surface` | `#F7F5EF` | `#0A0A09` | Elevated dossiers, cards, toolbars |
| `--jv-surface-sunken` | `#E7E4DC` | `#000000` | Sunken panels, inputs, secondary wells |
| `--jv-ink` | `#0A0A09` | `#F2F0E9` | Primary high-contrast text and solid glyphs |
| `--jv-ink-soft` | `#34332F` | `#C5C1B8` | Body text, explanations, narratives |
| `--jv-muted` | `#79766E` | `#77736C` | Folio headers, hashes, timestamps |
| `--jv-rule` | `rgba(10, 10, 9, 0.14)` | `rgba(242, 240, 233, 0.14)` | Structural 1px division hairlines |
| `--jv-rule-strong` | `rgba(10, 10, 9, 0.32)` | `rgba(242, 240, 233, 0.32)` | Focus outlines and boundary accents |
| `--jv-inverse-bg` | `#0A0A09` | `#F2F0E9` | Inverted background for active tabs and banners |
| `--jv-inverse-fg` | `#F2F0E9` | `#050505` | Inverted foreground text |
| `--jv-hatch` | `rgba(10, 10, 9, 0.22)` | `rgba(242, 240, 233, 0.22)` | Diagonal hatch texture pattern |
| `--jv-dot` | `rgba(10, 10, 9, 0.16)` | `rgba(242, 240, 233, 0.16)` | Stipple dot matrix texture pattern |

> [!NOTE]
> **Strictly Monochrome**: JackVerse maintains an authored black-and-white print identity. All alerts, status indicators, and high-consequence states are rendered through typographical weight, inverted ink contrast, and texture badges (`texture-hatch` / `texture-dots`)—never chromatic accents.

---

## 3. Zero-Flash Theme Initialization & Reactive Switching

### Bootstrap Architecture
To eliminate theme flashing (FOUC) on cold start, `web/index.html` injects an immediate inline script before any stylesheets or DOM elements parse:

```html
<script>
  (function() {
    try {
      var t = localStorage.getItem('jv_theme');
      if (t === 'ink' || t === 'paper') {
        document.documentElement.dataset.theme = t;
      } else {
        document.documentElement.dataset.theme = 'paper';
      }
    } catch (e) {
      document.documentElement.dataset.theme = 'paper';
    }
  })();
</script>
```

### Authored Theme Control (`ThemeSwitch.tsx`)
- Displayed prominently in the top editorial bar: `◐ PAPER / INK`.
- Features active underline indicator, high-contrast toggle, and tactile scaling.
- Applies a temporary 400ms theme wash transition (`theme-wash` class) while respecting `prefers-reduced-motion`.
- React hook `useTheme` employs a `washTimerRef` to prevent timer race conditions during rapid toggles, a `MutationObserver` on `document.documentElement` (`attributeFilter: ['data-theme']`), and a window `storage` listener to guarantee real-time synchronization across multi-tab sessions and programmatic DOM updates.

---

## 4. Contextual Book-Spine Navigation (`NavRail.tsx`)

The navigation system draws direct inspiration from the spine of a cloth-bound dossier or journal.

### Desktop Behavior (`>= 768px`)
- **Resting State**: Slim 64px (`w-16`) spine displaying numbered section indices (`01` through `06`) and the minimal `JV` monograph.
- **Engaged State**: Smoothly expands to 240px (`w-60`) on hover or keyboard `:focus-within`, revealing full section titles (`01 Home`, `02 Missions`, `03 Opportunities`, `04 Needs you`, `05 My Context`, `06 Activity`).
- **Contextual Folios**: When drilling into a specific Mission (`/missions/:id`) or Case (`/cases/:id`), the spine renders a contextual return folio (`← Missions Index` or `← Parent Mission`) allowing immediate hierarchical navigation without cluttering the main content canvas. Parent Mission targets dynamically resolve the parent `mission_id` via `useCase(caseId)`.
- **Dynamic Live Counters**: Section `04 Needs you` integrates `NumberRoll`, rolling animated digits when pending approval counts change.

### Mobile Drawer Behavior (`< 768px`)
- Desktop spine gracefully hides (`hidden md:flex`).
- Header displays a compact `☰ 01-06` trigger.
- Opens a full-screen editorial drawer with oversized folio typography, allowing single-tap navigation with automatic drawer dismissal.

---

## 5. Kinetic Primitives & Native Motion System

JackVerse eschews heavyweight animation libraries in favor of native browser primitives and CSS physics:

### 1. View Transitions API (`startViewTransition`)
- Implemented in `web/src/utils/transitions.ts`.
- Progressive enhancement: if `document.startViewTransition` is supported and `prefers-reduced-motion` is not active, route and state transitions execute seamless cross-dissolves and morphs; otherwise falls back instantly.

### 2. Physical Button Press (`TactileButton.tsx`)
- Standardized tactile feedback using `active:scale-[0.97]` and discrete 150ms transitions.
- High-contrast states: `primary` (solid ink on bg), `secondary` (surface with strong rule), `outline` (hairline rule with hover contrast), and `danger` (inverted high-contrast boundary).

### 3. State Continuity (`MorphText.tsx` & `NumberRoll.tsx`)
- `MorphText.tsx`: Whole-label state continuity. Smoothly cross-fades and translates labels during state changes without jarring reflow, using explicit `transition-[transform,opacity]`, with instant swaps for reduced-motion users.
- `NumberRoll.tsx`: Tabular numeric wheels with genuine two-layer spatial roll (`translateY(0) -> -100%` for old, `translateY(100%) -> 0` for new) triggered only on actual value change, with instant replacement for reduced-motion users.

### 4. High-Consequence Slider (`DragToAuthorize.tsx`)
- Consequential actions require deliberate physical commitment: an 88% drag travel threshold with pointer capture and spring return.
- Includes accessible keyboard confirmation (`Space` / `Enter`) and standard button fallback.

### 5. Restrained Ambient Signature (`AmbientCanvas.tsx`)
- Ultra-lightweight HTML5 2D canvas rendered behind the main stage.
- Subtle architectural crosshairs (`+`) rendered in `--jv-rule` with gentle pointer proximity displacement.
- Fully non-blocking: pauses RAF loop completely when the document is hidden (`visibilitychange`), when user is idle, or when points settle at resting position; cancels RAF under `prefers-reduced-motion`.

---

## 6. What JackVerse Will Never Look Like (The Brutal Anti-Patterns)

To protect the integrity of the design system across future phases, the following anti-patterns are strictly forbidden:

1. **NO SaaS Dashboard Generic Cards**: No 12px or 16px rounded rectangular cards floating on light grey with diffuse multi-layer drop shadows (`shadow-xl`).
2. **NO Synthetic AI Chat Bubbles**: No conversational assistant chat threads with typing indicators, pulsating glowing spheres, or fake "Thinking..." animations.
3. **NO Neon Accents or Silicon Valley Purples**: No gradient text, no indigo/violet brand buttons, no teal highlights. Color is restricted strictly to Paper and Ink monochrome values.
4. **NO Fake Domain Matching**: JackVerse does not display unpersisted opportunity match percentages, speculative fit scores, or artificial Case-Opportunity links before Phase 5 implements them.
5. **NO Unconsented Data Leaks**: Context vault facts marked `PERSONAL` or `SENSITIVE` are never rendered in full plaintext on load; they require intentional human reveal and purge immediately on unmount.

---

## 7. Four-Surface Prototype Gate & Screenshot Critique

Before propagating across all application routes, Design V2 established an empirical **Four-Surface Prototype Gate**. All 16 production screenshots were captured against real FastAPI backends with synthetic data at 1440x900 (desktop) and 375x812 (mobile) in both Paper and Ink identities (`docs/prototypes/v2/`):

### Surface A: Intent Entry & Dispatch Broadsheet (`/`)
- **Paper 1440**: Broad editorial canvas with Instrument Serif headline (*"What do you want JackVerse to move forward?"*), high-contrast dispatch prompt, action required callout, and active mission ledger.
- **Ink 1440**: Photographic darkroom feel; deep blacks (`#050505`) with crisp, luminescent cream type (`#F2F0E9`).
- **Mobile 375 (Paper/Ink)**: Refined header with compact `JACKVERSE` brand and responsive drawer trigger.

### Surface B: Mission Dossier (`/missions/:id`)
- **Dossier Hierarchy**: Displays Mission Kind, Status badge, Title in Instrument Serif, Case roster with quick-transition buttons, and chronological event chronicle.

### Surface C: Human Governance Gate (`/approvals`)
- **Governance Dossier**: Left column decision index, right column formal action audit card.
- **Critical Refinement**: The action parameters inspector pre block was constrained with `max-h-28 overflow-y-auto` and header metadata now includes the full cryptographic SHA256 fingerprint, ensuring the constitutional boundary summary and `DragToAuthorize` slider remain immediately accessible above the fold.

### Surface D: Personal Context Vault (`/context`)
- **Vault Index**: Job Application Readiness readiness gauge, categorical fact groupings (Identity, Finance, Professional).
- **Privacy Architecture**: Sensitive values remain masked (`••••••••••••`) until explicit on-demand reveal; inline supersede editor handles ETag concurrency without page reload.

---

## 8. Verification & Performance Budget

Design V2 preserves 100% of JackVerse's functional and operational contracts:
- **TypeScript**: Zero errors (`tsc -b --noEmit`).
- **ESLint**: Zero warnings or errors across `web/src`.
- **Unit Tests**: All passing in Vitest (theme synchronization, spine expansion, privacy masking, ETag concurrency, slide authorization, humanized event presentation, reduced motion, keyboard accessibility).
- **End-to-End Tests**: Passing in Playwright (navigation flow, mobile drawer, drag-to-authorize, real integration with live FastAPI).
- **Backend Regression**: 769/769 tests passing in Docker `harness` container.
- **OpenAPI Drift**: Zero drift (`export_caseworker_openapi.py` diff clean).
- **Bundle Footprint**: Maintained well within <= 15% budget constraint.
- **Docker Production**: Web container serving on `127.0.0.1:3001` with clean SPA routing and zero-flash theme bootstrap.
