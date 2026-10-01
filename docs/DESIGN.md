# JackVerse Caseworker Design System
**Visual Language: Monochrome Kinetic Editorialism**

---

## 1. Philosophy & Aesthetic Intent

JackVerse Caseworker rejects conventional "SaaS minimalism" and "AI agent demo" cliches. The interface is engineered as a **designed editorial object** rather than an assembly of cards.

### Core Tenets
1. **Monochrome Kinetic Editorialism**: Contrast, typography as architecture, negative space, and disciplined 1px borders define structure. Color is completely eliminated—there are no blues, purples, emerald greens, neons, glassmorphic blurs, or AI gradients.
2. **Anti-Card Discipline**: Information lives in structured dossiers, lists, hairlines, and asymmetric typographic layouts. We explicitly prohibit nested rounded-xl cards with drop shadows.
3. **Truthful Agent States**: The system never simulates thinking, searching, or artificial latency. If no network operation is occurring, the agent is reported as idle/passive. The interface reflects reality without anthropomorphic theater.
4. **Physicality & Tactile Authorization**: User consent is physical. Routine operations use calibrated micro-recessed triggers (`scale(0.96)`). Irreversible or consequential external operations use deliberate drag-to-authorize controls with physical resistance and spring-resets.

---

## 2. Color & Surface Tokens

All tokens are defined in CSS custom properties (`web/src/styles/tokens.css`) and integrated into Tailwind configuration:

| Token | Hex / CSS Value | Semantic Role |
| :--- | :--- | :--- |
| `canvas` | `#050505` | Primary deep canvas background |
| `ink` | `#0A0A0A` | Slightly elevated panel surface (dossiers, input fields) |
| `paper` | `#F4F3EF` | Primary high-contrast text and interactive surface |
| `pure` | `#FFFFFF` | Maximum typographic contrast, active titles, and highlights |
| `grey-100` | `#E5E5E5` | High-contrast subtext |
| `grey-300` | `#A3A3A3` | Secondary text, descriptions, table metadata |
| `grey-500` | `#737373` | Muted captions, machine IDs, timestamps, inactive borders |
| `grey-700` | `#262626` | Structural 1px division lines and borders |

### Status Representation (Colorless)
Status is communicated exclusively through weight, outline, fill, pattern, and typography:
- **`ACTIVE`**: Solid outline with filled dot `● ACTIVE` (`border border-paper text-paper bg-canvas`).
- **`BLOCKED / PENDING`**: Genuine 45-degree crosshatch pattern (`/// BLOCKED`) created with CSS repeating linear gradient (`repeating-linear-gradient(45deg, #262626, #262626 4px, #050505 4px, #050505 8px)`).
- **`PAUSED / INACTIVE`**: Muted outline `— PAUSED` (`border border-grey-700 text-grey-500 bg-transparent`).
- **`RESOLVED / COMPLETED`**: Inverted fill `✓ COMPLETED` (`bg-paper text-canvas font-semibold`).

---

## 3. Three-Voice Typography System

Typography functions as structural architecture. Three typefaces serve three distinct voices:

```
+-----------------------------------------------------------------------+
| VOICE 1: DISPLAY (Instrument Serif)                                    |
| Purpose: Editorial titles, intent dispatch prompts, dossier headers   |
| Characteristics: Editorial elegance, high contrast, serif structure  |
+-----------------------------------------------------------------------+
| VOICE 2: INTERFACE (Geist Sans)                                       |
| Purpose: Navigation, button labels, form inputs, primary content body |
| Characteristics: Clean, geometric, neutral reading rhythm             |
+-----------------------------------------------------------------------+
| VOICE 3: MACHINE / METADATA (Geist Mono)                              |
| Purpose: Timestamps, hash digests, IDs, status tags, audit ledgers    |
| Characteristics: Monospaced, tabular numerals, technical authority   |
+-----------------------------------------------------------------------+
```

### Local Bundling
Fonts are strictly bundled locally via npm packages (`@fontsource/instrument-serif`, `@fontsource/geist-sans`, `@fontsource/geist-mono`). No external Google Fonts CDN or runtime webfont requests are permitted.

---

## 4. Layout Architecture & Responsive Strategy

### Left Rail (Desktop) & Responsive Header Drawer (Mobile)
- **Desktop (`>= 768px`)**: Fixed 224px (`w-56`) left navigation rail with numbered routes:
  - `01 HOME`
  - `02 MISSIONS`
  - `03 OPPORTUNITIES`
  - `04 NEEDS YOU` (with live dynamic badge for pending approvals)
  - `05 MY CONTEXT`
  - `06 ACTIVITY`
  - System footprint footer (`SYS // RUNTIME: v0.4.0`, `MODEL // DETERMINISTIC`).
- **Mobile (`< 768px`)**:
  - The fixed sidebar collapses (`hidden md:flex`) to prevent screen-width starvation.
  - Header displays compact `☰ 01-06` toggle alongside protocol status and glyph.
  - Clicking `☰ 01-06` opens an editorial overlay drawer containing full numbered destinations.
  - Selecting any destination automatically closes the drawer and transitions the route.
  - Canvas padding scales gracefully (`p-4 sm:p-6 md:p-8 lg:p-12`).

---

## 5. Kinetic & Motion Language

- **Micro-interactions**: Discrete 150ms transitions (`duration-150 ease-out`).
- **Tactile Button Press**: `active:scale-[0.97]` provides instant physical feedback.
- **State Continuity (`MorphText`)**: Transitions between state labels (e.g. from `INVESTIGATING` to `RESOLVED`) smoothly animate character-by-character without abrupt layout shifts.
- **Drag-to-Authorize Kinetic Resistance**:
  - Linear horizontal track with pointer capture.
  - 88% travel threshold required to trigger execution.
  - Incomplete travel snaps back to 0% via spring physics on release.
  - Immediate visual lockout during asynchronous submission prevents double-click race conditions.
  - Accessible keyboard confirmation via `Space` / `Enter` or explicit button fallback.

---

## 6. Signature Interactions

### Halaska "Needs You" Approval Surface
Consequential and standard decisions are clearly differentiated:
1. **Low-Risk / Routine Decisions**: Immediate tactile trigger `[ Approve ]` or `[ Reject ]`.
2. **Consequential / High-Impact Decisions** (external transmissions, legal bindings, financial commitments): Flagged with `/// BLOCKED` and high-impact conflict gates. Executed solely via `DragToAuthorize`.
3. **Truthful Execution Boundary**: Upon authorization, the UI explicitly displays *"Authorization recorded. Execution is not connected in this phase."* The interface never claims external dispatch or autonomous agent activity when execution is unplumbed.

### Truthful `AgentGlyph`
- Renders an animated pulse if and only if an active HTTP mutation or query refetch is taking place.
- In passive state, displays a solid static indicator with exact ISO timestamp.
- Never fakes thinking, reasoning, or searching states.

### On-Demand Context Privacy & Zero-Retention Memory
- In accordance with the Phase 3 and Phase 4.1 backend privacy contract, `GET /api/v1/context/facts` returns sanitized summaries (`FactSummaryResponse`) that omit raw `value` and `source_reference` for sensitive facts. SQLite stores facts as plain JSON text; no false claims of at-rest encryption or cryptographic on-demand decryption are made in the UI or documentation.
- Fact values are displayed with a masked preview (`PRIVATE ••••••••••••`).
- When the user explicitly triggers `[ Reveal ]`, the UI fetches `GET /api/v1/context/facts/{id}` to display protected detail on demand.
- The React Query cache for sensitive details is configured with `staleTime: 0, gcTime: 0` and is purged immediately upon clicking `[ Mask ]` or when the component unmounts.
- Sensitive values are never persisted to `localStorage` or `sessionStorage`. Fact verification and rejection operate strictly from summary metadata without requiring raw value revelation.

---

## 7. Banned Patterns Checklist

- [x] **NO** accent colors (no blue CTA buttons, purple sparkles, green badges, red alert boxes).
- [x] **NO** rounded-xl card grids with drop shadows.
- [x] **NO** glassmorphism, blur backdrops, or gradient borders.
- [x] **NO** anthropomorphic agent personas or fake "thinking..." loops.
- [x] **NO** runtime CDN font links (local `@fontsource` only).
- [x] **NO** local storage of unencrypted credentials, tokens, or sensitive context vault values.
- [x] **NO** fake "Start a Case" action on Opportunities (strict separation maintained until Phase 5 backend foreign-key model).

---

## 8. Prototype Critique & Visual Audit Log

Visual verification was conducted on automated Playwright captures in `docs/prototypes/`:

| Prototype | Desktop (1440x900) | Mobile (375x812) | Audit Finding & Resolution |
| :--- | :--- | :--- | :--- |
| **A: Dashboard** | `prototype_a_desktop.png` | `prototype_a_mobile.png` | **Finding**: Mobile viewport initially squeezed into 150px due to fixed 224px sidebar.<br>**Resolution**: Converted `NavRail` to responsive `hidden md:flex`, added mobile header toggle `☰ 01-06`, reduced padding to `p-4`. Verified full-width typographic layout. |
| **B: Mission Dossier** | `prototype_b_desktop.png` | `prototype_b_mobile.png` | **Finding**: Dossier metadata and case list fit cleanly on desktop. On mobile, metadata wrap was optimized with `flex-wrap` and compact badge tags.<br>**Resolution**: Clean editorial hierarchy achieved on both form factors. |
| **C: Needs You** | `prototype_c_desktop.png` | `prototype_c_mobile.png` | **Finding**: Consequential gate header collided on 375px (`APPROVAL QUEUE // CONSTITUTIONAL GATE 1 PENDING DECISION`). Personal placeholder data used.<br>**Resolution**: Changed header container to `flex-col sm:flex-row gap-1`, adjusted dossier card padding to `p-5 md:p-8`. Sanitized all fixture data to synthetic persona Alex Mercer (`alex@example.test`, Northstar AI, `Synthetic_CV.pdf`). Replaced execution wording with truthful connection disclaimer. |

### Visual Artifacts
- [Prototype A Desktop](docs/prototypes/prototype_a_desktop.png)
- [Prototype A Mobile](docs/prototypes/prototype_a_mobile.png)
- [Prototype B Desktop](docs/prototypes/prototype_b_desktop.png)
- [Prototype B Mobile](docs/prototypes/prototype_b_mobile.png)
- [Prototype C Desktop](docs/prototypes/prototype_c_desktop.png)
- [Prototype C Mobile](docs/prototypes/prototype_c_mobile.png)
