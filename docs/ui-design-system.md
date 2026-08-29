# internsHELPer interface system

This is the implementation reference for the approved **Refined command center** redesign. It
keeps internsHELPer company-first and page-based, with the familiar application shell from visual
Direction A and the stronger selection, focus, drawer, and minimum-window behavior explored in
Direction C.

## Principles

1. **Company identity wins.** Company name, group, posting count, and newest activity remain
   readable before secondary controls.
2. **Browse, then act.** The company Inbox is the primary reading surface; the application pipeline
   is the active-work surface; exception lenses remain available but quiet.
3. **Progressive density.** Scan a company, expand its roles, then inspect one posting in the
   drawer. Do not repeat every field at every level.
4. **Separate approvals by noun and space.** Board-group approval and collection-source approval
   use separate panels, headings, explanatory text, and action labels.
5. **Exceptional states lead.** Failures, flags, and unresolved duplicates surface first. Healthy
   or historical information is visually subordinate.
6. **Reversibility is explicit.** Consequential actions explain their effect and retain the current
   Undo mechanisms.

## Foundation

- Typography: existing local Geist 500/600/700 assets. Body 13–14px; metadata 11–12px; page title
  24px; drawer title 22px.
- Spacing scale: 4, 6, 8, 10, 12, 16, 20, 24, 32, 40px.
- Radius scale: 5px for compact controls, 7px for inputs/buttons, 9px for primary surfaces, pill
  radius only for compact status/count badges.
- Control heights: 32px compact, 36px standard. Icon controls are at least 32×32px.
- Shadows: borders carry most hierarchy. A small shadow is reserved for floating drawers, menus,
  selected drag items, and toasts.
- Accent: restrained indigo for selection, focus, navigation, and primary actions. Accent never
  represents company priority or rank.
- Semantic colors: blue Applied, violet Interviewing, green Offer/healthy, red Rejected/error,
  amber warning/quiet. Every semantic state includes text or an icon, never color alone.

## Themes

Light mode uses a cool gray workspace, white primary surfaces, high-contrast near-black text, and
subtle indigo selection. Dark mode uses separate charcoal surfaces and lighter borders; it is not
an inversion. Muted and faint text must maintain at least 4.5:1 contrast at normal text sizes.

## Layout and minimum-window behavior

- Normal desktop: 190px labeled sidebar; page content uses the available width. The Board is a
  two-track workspace: company Inbox on the left and a 2×2 pipeline on the right.
- At 1143px wide and below (including the 980×640 minimum): the sidebar becomes a 62px icon rail. The Board exposes an explicit
  Opportunities/Pipeline switch so neither track is squeezed. Company names remain visible and
  the document has no horizontal page overflow.
- Wider desktop: Inbox and pipeline grow proportionally; content does not become oversized.
- Page scrolling remains the primary scroll model. Avoid independently scrolling application
  panes. Large Board lenses paginate on the server (100 rows for tables/history, 25 duplicate
  decision groups); tables may scroll horizontally inside their own bounded surface.
- Drawer: 440px inspection drawer on normal desktop; up to 55% of the viewport near minimum size.
  Long drawer content scrolls within the drawer while the background is inert.

## Component contracts

- Sidebar: labeled destinations, `aria-current`, accessible Health exception status, theme cycle,
  and a compact rail at minimum width.
- Page headers: eyebrow/context, 24px title, short supporting copy, quiet right-aligned actions.
- Buttons: primary, secondary, ghost, danger, and icon variants with visible hover, focus, loading,
  and disabled states.
- Segmented controls: primary presentation choice only. Board maintenance lenses live in a quiet
  Exceptions disclosure.
- Company areas: semantic disclosure button, company identity first, count/newest second, group
  selector last. Expanded postings are compact rows ordered by existing server semantics.
- Posting cards: title first; location, age, and recognizable source second; explicit selected and
  drag states; icon actions have accessible names.
- Pipeline lanes: compact stage header, count, hide action, populated cards, and a labeled empty
  drop target. Hidden lanes remain keyboard-restorable.
- Tables: sticky header, bounded surface, readable row hover/focus, labeled external-link controls.
- Toasts: `role=status`, descriptive text, visible Undo, focusable controls, and restrained motion.
- Drawer: dialog semantics, labeled close control, focus entry/trap/restore, readable role overview,
  grouped application form, explicit saved/loading state, and separated flag/dismiss sections.
- Empty/loading/error states: concise title, explanation, and next action where one exists.

## Page changes

- Board: new header hierarchy; Board/Table plus quiet Exceptions control; two-track desktop layout;
  explicit minimum-width Opportunities/Pipeline modes; semantic company disclosures; redesigned
  cards, lanes, duplicate review, and tables.
- Companies: two simultaneous, clearly named systems—Board grouping & discovery and Careers-board
  resolution—with action labels that state which system changes.
- Postings: labeled filters, clearer archive terminology, sticky table header, and recognizable
  source presentation.
- Sources: managed-connection cards, wrapped identifiers, quiet collection-rule disclosure,
  contextual removal confirmation, and a visible add-source workspace.
- Health: exception summary and exception-first ordering; healthy sources become a quieter
  disclosure with textual Healthy status.

## Interaction and accessibility requirements

- Preserve HTMX targets, out-of-band navigation/toast swaps, SortableJS lane attributes, expansion
  persistence, drag/drop, theme persistence, and all existing route contracts.
- Company, group, hidden-lane, and posting interactions are real buttons or keyboard-operable
  controls with appropriate accessible names and expanded/current state.
- Opening the drawer records the triggering element, focuses the dialog, traps Tab, makes the
  background inert, and restores focus on close. Escape and scrim close it.
- HTMX loading and error states remain visible. Saving in the drawer retains a status message.
- Motion uses 120–180ms ease-out transitions and is removed under `prefers-reduced-motion`.

## Verification criteria

The complete web suite must pass. Focused assertions cover semantic disclosures, accessible icon
names, current navigation, drawer dialog semantics, labeled filters, and the two company systems.
The optional Playwright suite (`pytest -m browser`) covers the 1143/1144px boundary, a cross-page
980/1143/1144/1440px overflow matrix, popover/drawer keyboard behavior, quick pipeline changes,
HTMX focus restoration for replaced controls, and progressive source-removal confirmation.
Manual verification covers every Board lens, company expansion, pipeline lanes and drag feedback,
drawer editing/actions, Companies approvals, Sources states, Health exceptions, light/dark/system
themes, keyboard-only operation, reduced motion, 980×640, normal and wide desktop, and pywebview.
