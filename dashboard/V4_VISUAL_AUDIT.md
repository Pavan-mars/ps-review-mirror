# V4 visual audit — what we have, what to change

04-Aug-2026. Counts extracted from `src/v4` source, not estimated.

---

## 1. Current inventory

**Totals across V4: 32 sub-tabs, 43 tables, 81 panels, 33 stat tiles, 51 charts.**
The imbalance is the finding: **more tables than charts, and nearly twice as many
panels as charts.** Most panels are carrying a table or a number, not a picture.

### PS1 — Failure Prediction · 6 sub-tabs

| Sub-tab | Charts | Tables | Panels |
|---|---|---|---|
| Fleet status | Trend, RankBars, Donut, FunnelView | 0 | 4 |
| Depots | ColumnBars, RankBars, Treemap | 1 | 3 |
| Devices | — | 1 | 0 |
| Components | RankBars, Bubble | 1 | 2 |
| Why | ColumnBars ×3, RankBars ×2, Matrix | 1 | 7 |
| How we know | RankBars ×2 | 2 | 4 |

**Totals:** 19 charts · 6 tables · 23 panels · 4 stats.
Best-balanced of the five. "Devices" is a bare table with no visual at all.

### PS2 — Failure Patterns & Cascades · 7 sub-tabs

Fleet impact · Cascades · Where it happens · Devices · Components & repairs ·
Component relationships · How we know

**Totals:** 21 charts (Trend ×4, ColumnBars ×5, RankBars ×9, Donut, Matrix ×2) ·
**15 tables** · **37 panels** · 10 stats.

The heaviest screen in the product and the most table-bound. Panels such as
*Top precursor patterns*, *Most central subsystems*, *Repairs logged over time*
and *Did the repair help* are all carrying tables where the data is inherently
relational or temporal.

### PS3 — Root Cause Analysis · 6 sub-tabs

What breaks · Devices · Where it happens · What repeats · How we know ·
Root cause & severity

**Totals:** 7 charts (ColumnBars, RankBars ×5, Donut) · **10 tables** · 10 panels · 7 stats.

**Worst chart-to-table ratio in the product: 0.7 charts per table.** Sections
such as *From fault signals to breakdowns*, *How soon the same component comes
back* and *Average treatment effect on repeat within 30 days* are analytically
the richest content we have, presented almost entirely as rows.

### PS4 — Anomaly & Outlier Analysis · 6 sub-tabs

| Sub-tab | Charts | Tables |
|---|---|---|
| Fleet status | ColumnBars, RankBars, Donut, FunnelView | 0 |
| Depots | RankBars, Bubble, Treemap | 1 |
| Devices | — | 1 |
| Peer groups | ColumnBars, RankBars | 1 |
| Repeat flags | ColumnBars, RankBars | 1 |
| How we know | — | 3 |

**Totals:** 11 charts · 7 tables · 20 panels.
Structurally the closest to PS1. "Peer groups" is the single most under-served
screen: clustering output shown as bars.

### PS5 — Remaining Life & SLA · 5 sub-tabs

Fleet status · Devices · Components · Model quality · How we know

**Totals:** 6 charts (ColumnBars, RankBars ×4, Donut) · 3 tables · 9 panels ·
**12 stats** — the most stat-tile-heavy screen, and the fewest charts per tab.
Survival data with almost no survival visual.

### Device 360

1 Trend · 2 tables · 2 panels, plus the WHERE/WHEN/WHAT brief.

---

## 2. How the tabs should be reorganised

Four changes, in order of value.

**(a) Every screen has a "How we know" tab. Merge them into one.**
Five separate methodology tabs — 12 tables between them — each read by almost
nobody, each costing a tab slot on the screen the client actually looks at.
One estate-wide **Evidence** tab, with a problem-statement slicer, frees a slot
on all five screens and makes model quality comparable across PS for the first
time. `ChipSlicer` does the switching.

**(b) "Devices" is a bare table on PS1, PS4 and PS5.**
Three screens where the busiest tab has no visual. Each should lead with a
distribution — the shape of the risk, not just its ranking — then the table
below it. `Histogram` costs one line and answers "is this a fleet with a few
bad devices or a broadly ageing one", which no ranked list can.

**(c) PS2 carries 37 panels across 7 tabs. Split it.**
It is doing two jobs: *what the estate lost* (impact, where, devices) and
*how failures relate* (cascades, precursors, subsystem relationships). The
second is the actual PS2 question. Promote **Relationships** to its own top-level
area and the remaining tabs become readable at four panels each.

**(d) PS5 shows 12 stat tiles and 6 charts.**
Stat tiles are the right form for a headline and the wrong one for a
distribution. Half of those tiles are summarising a spread that should be drawn.

---

## 3. Which visuals to replace, and what each buys

Ranked by insight gained per line changed.

| # | Where | Today | Replace with | What it adds |
|---|---|---|---|---|
| 1 | PS3 · *From fault signals to breakdowns* | 4 stacked bars | **`DecompositionTree`** | The funnel is a decomposition — 15.0M events → 7.6M on live devices → 54,239 episodes. A tree shows what each stage removed *and* what it split into, which the bars cannot. |
| 2 | PS2 · *Top precursor patterns* | table of 12 rows | **`NetworkGraph`** | "A precedes B" is a graph. As rows, the reader must hold twelve pairs in their head to see that one subsystem is upstream of four others. As a graph it is immediate. |
| 3 | PS1 · *Strongest drivers* | RankBars | **`KeyInfluencers`** | Bars show magnitude only. Drivers have direction — a feature pushing risk *down* is not a small version of one pushing it up. Signed bars from a centre line say which. |
| 4 | PS2 · *Did the repair help* | before/after table | **`SlopeChart`** | The whole question is "who moved". A slope draws the change; a table makes the reader subtract 30-day-before from 30-day-after, row by row. |
| 5 | PS5 · *Every device, worst first* | table + 12 stat tiles | **`Histogram`** + table | Survival data has a shape. "3,235 validators, 2,504 past expected life" is a distribution collapsed into two numbers. |
| 6 | PS4 · *Peer groups* | ColumnBars + RankBars | **`Radar360`** | A cluster is a profile across several normalised measures. Radar is the one form that shows *why* two clusters differ rather than that they do. Requires normalising axes first. |
| 7 | PS2 · *Which subsystems fail together* | Matrix | **`HeatGrid`** with `diverging` | The current matrix uses a single ramp for a correlation. Correlation has a true zero; it needs a neutral midpoint so negative and positive read as opposites, not as small and large. |
| 8 | PS4 · *Repeat flags* | ColumnBars | **`BumpChart`** | "Which depots are worst this week" is a rank question. A bump chart shows a depot climbing four places; grouped bars hide it. |
| 9 | PS1 · *Condition by risk tier* | Matrix | **`HeatGrid`** | Same grid, sequential ramp, but with the row/column totals the current Matrix drops. |
| 10 | PS2 · *Out-of-service hours by day* | Trend | **`AreaTrend`** | Volume lost over time is a magnitude, not a direction. Area states the quantity. |
| 11 | PS1/PS4 · risk tier over time | Trend | **`StepLine`** | Tiers are read once a day and hold. A sloped line between two daily readings implies values that were never measured. |
| 12 | Depot tables (PS1, PS2, PS4) | static columns | **`Spark`** column | The mockups show a 3-day trend sparkline per depot row. Needs a per-depot daily series — see the gap below. |
| 13 | All screens | fixed scope | **`ChipSlicer` / `RangeSlicer`** | Filters currently live inside panels or not at all. Above the charts, with visible state, so nobody quotes a filtered number as a fleet total. |
| 14 | PS1 · *Components* | Bubble | **`ScatterPlot`** with `trend` | Age against risk is two measures; the third dimension is currently decorative. A fitted line with r² reported answers "does age actually predict risk here" — and says so honestly when it does not. |

### Data gaps that block three of these

- **#12 sparklines** need a per-depot daily series. `station-summary` is one row
  per depot with no time dimension. Needs a new endpoint or a widened view.
- **#8 bump chart** needs rank by period. `ps4/weekly-facility` has the periods;
  rank must be computed client-side or added server-side.
- **#6 radar** needs cluster profiles on a shared 0–1 scale. `ps4/cluster-profile`
  exists — its columns must be checked before wiring.

### Sequencing

Items 1–5 are the highest value and need no new data. Items 7, 9, 10, 11, 14 are
one-line swaps on existing feeds. Items 6, 8, 12 need the data work above.
