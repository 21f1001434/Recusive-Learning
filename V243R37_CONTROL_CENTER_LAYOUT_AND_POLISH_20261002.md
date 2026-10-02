# V243R37: the Control Center fits every screen (2026-10-02)

## What was asked

"Improve the UI and fix the overflowing UI elements, and any other improvement you can think of."

## What was wrong

A layout audit measured the real Control Center in a browser at 9 screen sizes (1920 to 390 px) on all 6 tabs. It found **629 problems**:

| Problem | Where and why |
|---|---|
| Tile values broken inside a word | "gpt-oss-120b" on three lines; "Stopped", "Blocked", "All sections" cut off. At 1366–1600 px the sidebar (340 px) and the docked chat (390 px) left the dashboard about 570 px for 6 tile columns. |
| Panels spilling out of the page | The Agent Live View, the mission trace and the run list were wider than their panel. The live-view layout had a fixed minimum of 720 px. |
| Wrapped buttons and badges | "Backend ready", "Needs correction", "Teach mapping" and "Run full E2E" wrapped onto two lines. |
| Tables needing sideways scroll on a desktop | Headers could not wrap, so the row plan, phase coverage, skill vetting and live input map all scrolled sideways. |
| Long IDs and paths not wrapping | A run ID or a Windows path ran past the panel edge, and on a phone widened the whole page. |
| On a phone, the controls before the dashboard | The full sidebar (about 3 screens) came before the status. |
| "Open the Teach panel" did nothing | The chat link looked for the Teach controls in the main area; they are in the sidebar. |
| Toasts vanishing early | An older message's timer hid a newer message. |

## What changed

### Nothing overflows

- **Grids size to the space the dashboard actually has, not to the screen width.** The tiles, panel pairs, live view, counters, mission trace and stats use CSS container queries on the main area. With the sidebar and chat docked or not, they always get the right number of columns.
- **Tiles never break a word.** They show as many columns as fit (each at least 172 px). Values stay on one line, and a long detail is clamped to two lines. Hover any tile for its full text.
- **Tiles show their state with a coloured edge.** Running, Ready and PASS are green; Blocked, Failed and Error are red; Paused, Stale and Not run are amber.
- **Badges, buttons and tabs never wrap.** Button pairs in the sidebar sit side by side when they fit and stack when they don't, instead of cutting the label.
- **Long values wrap where they land.** Run IDs, paths, JSON and any long token wrap instead of widening the page. Flex and grid items that hold text may shrink.
- **Tables wrap their cells.** A table scrolls inside its panel only when it really has too many columns. Phase keys show as names ("Source Document Type"), with the key in the tooltip.
- **Panel widths adapt to the screen size:**

  | Screen | Sidebar | Chat |
  |---|---|---|
  | ≥ 1700 px | 320 px | 400 px, docked |
  | 1500–1699 px | 300 px | 370 px, docked |
  | 1350–1499 px | 280 px | 350 px, docked |
  | 1100–1349 px | 280 px | drawer (R35) |
  | below 1100 px | drawer | drawer |

### Easier to use

- **Collapsible sidebar.**
  - On a desktop, ☰ hides the sidebar and the dashboard gets about 300 px more; this is remembered.
  - Below 1100 px the sidebar is a drawer opened by **☰ Controls**, so the dashboard comes first. Close it with ✕, Escape or a tap outside.
  - Starting, pausing or stopping a mission from the drawer closes it.
- **Foldable sidebar sections.** Click (or press Enter on) a section heading to fold it; this is remembered. The Teach section opens by itself when the agent asks for help or a phase review.
- **Tabs.**
  - The tab bar stays on screen as you scroll and keeps to one row.
  - Arrow keys, Home and End move between tabs.
  - The open tab is remembered and linkable (`…/#mission`).
- **The docked chat can be resized.** Drag its left edge (320 px to 42% of the screen); this is remembered. Double-click the edge to reset.
- **The browser tab title shows the agent's state:** "▶ 12/29", "⏸ Paused", "❓ Needs you", "⛔ Blocked" or "✅ Complete". You can see it from another tab.
- **Press `/` anywhere to type to the agent.** It opens the chat if it is hidden.
- **"Open the Teach panel" works.** It opens the sidebar (or drawer), unfolds the Teach section, scrolls to it and focuses the field list.
- **Toasts last long enough.** Each message gets its full time (longer for long messages). Click a toast to dismiss it.
- **Accessibility and polish.**
  - Keyboard focus rings.
  - Proper tab, tab-panel and separator roles.
  - An announced toast.
  - Dark native controls and slim dark scrollbars.
  - Reduced-motion support.
  - A favicon, and a `window.hipControlCenter` handle for automation (`showTab`, `toast`, `revealHumanAssistance`).

Both copies of the UI (`webui/` and `backend/webui/`) are identical, and every element ID is unchanged.

## Proof

The same audit after the change:

| Check | Before | After |
|---|---|---|
| Problems over 9 screen sizes × 6 tabs | 629 | **0** |
| Very long values (120-character paths and IDs) put into every cell, list, tile, heading and chat bubble, at 1920 / 1366 / 1024 / 390 px | page widened by up to 2,704 px | **0** spills, page never wider than the screen |
| Interactive states: collapsed sidebar, both drawers open (1024 and 390 px), chat resized | — | **0** problems |

Tests in `tests/test_v243r37_control_center_layout.py` (4) start the backend (which serves the Control Center) and drive a real browser:
- **Nothing overflows:** no element spills, leaves the viewport, wraps badly or is cut off at 1920 / 1440 / 1366 / 1024 / 390 px on the Preflight, Mission and Tasks tabs.
- **Long values wrap** without widening the page (1366 and 390 px).
- **Controls and memory:**
  - the sidebar collapses (the dashboard grows by at least 250 px);
  - keyboard tabs work and a deep link opens its tab;
  - the chat resizes;
  - folded sections, the open tab and the chat width survive a reload;
  - the Teach link opens a folded section;
  - the toast keeps its time;
  - the drawer opens and closes with Escape.
- **Both copies identical** and carrying the layout rules; the old Teach-link bug is gone.
