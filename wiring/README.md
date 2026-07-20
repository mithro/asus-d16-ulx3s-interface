# Wiring: KGPE-D16 &harr; ULX3S bench-controller harness

## One fixed harness

The ULX3S terminates **every** debug/control connector on the KGPE-D16 at
once, through a single cable harness that stays plugged in permanently.
There is no re-cabling to switch between roles (e.g. "SPI mode" vs "JTAG
mode") — all 41 signals across all 9 connectors are wired to their own GPIO
concurrently. This is what `wiring/make_pinmap.py` encodes and validates: it
builds the full 41-signal inventory with every signal's GPIO fixed explicitly
(50 usable pins after excluding the 6 reserved for the on-board ESP32), and
asserts the map is injective, complete, and satisfies the fixed constraints
below before ever writing output.

## Header split: ASpeed/BMC on J1, host/other on J2

The 41 signals are allocated so that **which physical header a pin sits on
tells you which side of the board it talks to**:

- **J1 (idx 0-10)** — ASpeed/BMC-side connectors only: `AST_JTAG1` (BMC ARM
  TAP), `AST_UART1` (BMC console), `BMC_FW1` (BMC SPI flash + straps). idx
  11-13 are ESP32-reserved and left unused, so J1 has no spare capacity for
  anything else.
- **J2 (idx 14-27)** — every other connector: `FU1` (host BIOS SPI flash),
  `COM1`/`COM2` (host serial), `AMD_HDT` (host CPU debug), `PANEL1` (front
  panel), `JUMPERS` (board straps). The BMC firmware *does* monitor
  `PANEL1`/`JUMPERS` in normal operation, but they're placed on J2 with the
  rest of the host/board-control signals anyway — J1 has no room (only 22
  usable pins after the ESP32 reservation, already fully spoken for by the
  three ASpeed connectors) and they're conceptually "board state", the same
  bucket as the host serial/debug connectors.

Within each header, each function's pins are contiguous, with an empty pin
left between function groups so the layout reads as distinct blocks rather
than one undifferentiated list — see `build_inventory()` in
`make_pinmap.py` for the exact index-by-index allocation and the gap
comments. `validate()` enforces the split itself: every ASpeed-side
connector's signals must resolve (by GPIO index) to J1, every other
connector's must resolve to J2 — a signal moved to the wrong header fails
loud with an `AssertionError` naming it.

J2 is tighter than J1: 14 columns / 28 pins carrying 24 signals across five
connectors leaves only 4 spare pins, not enough to put a full gap between
every individual *connector*. So the gaps sit between the four *function*
blocks (SPI / serial / JTAG / GPIO) instead, and connectors that share a
function are adjacent with no gap: `COM1` directly followed by `COM2`, and
`PANEL1` directly followed by `JUMPERS`.

## Source of truth

- **`wiring/pinmap.csv`** — the single source of truth for the signal-to-GPIO
  map: `connector,net,dir,via,gpio` per row, one row per signal. Regenerated
  (and re-validated) every run of `make_pinmap.py`.
- **`wiring/harness.svg`** — a three-column wiring diagram rendered from the
  *same in-memory signal list* that produces the CSV (nothing in the SVG is
  hand-authored data). Regenerate with:

  ```sh
  uv run wiring/make_pinmap.py --svg
  ```

  This also validates the inventory and rewrites `pinmap.csv` as normal;
  `--svg` only adds the diagram render on top.

## Reading the diagram

Each of the 41 signals is one row, grouped and labelled by connector
(`BMC_FW1`, `FU1`, `AST_UART1`, `COM1`, `COM2`, `AST_JTAG1`, `AMD_HDT`,
`PANEL1`, `JUMPERS`), with three columns:

1. **KGPE-D16 connector pin** (left) — `connector.net`, e.g. `BMC_FW1.CS0`.
2. **Harness element** (middle) — the physical path the signal takes,
   `Signal.via`: `direct`, `MAX3232`, or `1.27mm-adapter` (see below).
3. **ULX3S GPIO** (right) — the assigned pin, e.g. `gp7`, on the ULX3S
   J1/J2 GPIO headers (`gp0`-`gp27`, `gn0`-`gn27`; `gp11`-`gp13` and
   `gn11`-`gn13` are reserved for the on-board ESP32 and never assigned).

Rows are colour-coded by signal domain (legend in the diagram header):
**SPI** (`BMC_FW1`, `FU1`), **UART** (`AST_UART1`, `COM1`, `COM2`), **JTAG**
(`AST_JTAG1`, `AMD_HDT`), **GPIO** (`PANEL1`, `JUMPERS`). Arrowheads show
signal direction relative to the FPGA (`Signal.dir`): `in` flows
connector &rarr; harness &rarr; GPIO (the FPGA reads it); `out` flows
GPIO &rarr; harness &rarr; connector (the FPGA drives it). The diagram draws
an explicit white background rect so it stays legible regardless of the
viewer's light/dark theme.

## Why the harness elements differ

- **COM1 / COM2 (RS-232) &rarr; MAX3232.** These are true RS-232 levels
  (nominally &plusmn;12&nbsp;V, not TTL). ULX3S GPIO are 3V3-only; wiring
  RS-232 directly into a GPIO would exceed its absolute maximum rating and
  destroy the FPGA I/O (and/or fail to register a valid logic level in the
  other direction). A MAX3232 (or equivalent) level shifter is mandatory on
  both COM1 and COM2 — enforced in code by
  `validate()`'s assertion that every `COM1`/`COM2` signal has
  `via == "MAX3232"`.
- **AMD_HDT &rarr; 1.27&nbsp;mm-pitch adapter.** The AMD HDT (host CPU debug)
  header uses a non-standard 1.27&nbsp;mm-pitch connector, not the 2.54&nbsp;mm
  pitch used elsewhere on the board. A pitch-adapter cable is required to
  bring it out to standard 2.54&nbsp;mm jumper wire — enforced by
  `validate()`'s assertion that every `AMD_HDT` signal has
  `via == "1.27mm-adapter"`.
- **BMC_FW1 SPI &rarr; fixed at gp7-gp10.** The four core SPI signals
  (`CS0`, `SCK`, `MOSI/SPIDO`, `MISO/SPIDI`) stay pinned to `gp7`/`gp8`/`gp9`/
  `gp10` specifically (unlike the rest of the map, which is free to move as
  long as it stays on the correct header — see "Header split" above) so the
  harness stays compatible with existing spispy pogo/cable pinouts —
  enforced by `validate()`'s fixed-pin assertion.
- **Everything else &rarr; direct.** All other signals (BMC_FW1 straps, FU1
  host-BIOS SPI, AST_UART1, AST_JTAG1, PANEL1, JUMPERS) are native 3V3 and
  wire straight to a GPIO with no level shifting or adapter.

## Regenerating

```sh
uv run wiring/make_pinmap.py           # validate + print summary + write pinmap.csv
uv run wiring/make_pinmap.py --svg     # same, plus render wiring/harness.svg
uv run wiring/make_pinmap.py --headers # same, plus render the two header diagrams
                                        # (below) and write wiring/pi_pinmap.csv
```

Both invocations fail loud (`AssertionError`) if the map is invalid — e.g. a
duplicate GPIO, an unassigned signal, a reserved-pin collision, a fixed
BMC_FW1 pin drifting off `gp7`-`gp10`, a `COM1`/`COM2`/`AMD_HDT` signal
missing its required `via`, or a signal landing on the wrong header (an
ASpeed-side connector assigned a J2 pin, or vice versa — see "Header split"
above). The `--svg` path additionally asserts the drawn row count equals the
signal count (41), re-parses the written file as XML, and confirms every
`connector.net` label from `pinmap.csv` appears in the rendered SVG text.

## ULX3S and Raspberry Pi header reference (`--headers`)

`wiring/make_pinmap.py --headers` renders two additional, physical-header
diagrams and writes a second CSV, all derived the same way as the harness
diagram above: from structured data in the script, cross-checked against the
existing `pinmap.csv`/`Signal` data rather than duplicating it.

### `wiring/ulx3s-headers.svg` — ULX3S J1/J2 GPIO headers

The ULX3S exposes its 56 `gp[0..27]`/`gn[0..27]` GPIO on two 2.54&nbsp;mm
**2x20** double-row headers: **J1 carries idx 0-13, J2 carries idx 14-27**
(per [emard/ulx3s](https://github.com/emard/ulx3s) `MANUAL.md`; FPGA ball
assignments per `doc/constraints/ulx3s_v20.lpf`). The diagram draws each
header in **board row order** — every physical row top-to-bottom, so besides
the `gp`/`gn` signal pairs it also shows the **power/GND pins** each header
carries (from the PCB, `wiring/ulx3s-pads.json`): J1 has `2V5_3V3` supply
rails and `GND`; J2 has `+5V` (through the STPS2L40AF diodes) plus `+3V3` and
`GND`. Supply rows are orange, GND rows black (see legend). **J2 is oriented
as it sits on the board — idx 27 at the top, idx 14 at the bottom.** Every
`gp`/`gn` cell is joined against the same in-memory signal list that produces
`pinmap.csv` — any cell backed by an assigned signal is coloured by domain and
labelled `connector.net`; unassigned cells are grey.

Two things worth calling out explicitly:

- **`gp`/`gn` idx 11-13 are shared with the on-board ESP32** (`wifi_gpio26`/
  `33`/`35`) and are excluded from the usable pool entirely (`ESP32_RESERVED`
  in `make_pinmap.py`, `validate()` asserts no signal ever lands there). The
  diagram shades these distinctly and never shows them as assigned.
- **`gp`/`gn` idx 14-17 double as the onboard ADC channels** (`AIN0`-`AIN7`
  across the four differential pairs, per `ulx3s_v20.lpf`). The harness
  *does* assign most of these indices — after the J1/J2 reallocation they
  carry `FU1` (host BIOS SPI: `gp14`-`gp16`, `gn14`-`gn15`) and the start of
  `COM1` (`gp17`, `gn17`), with `gn16` left as the gap between the two
  function blocks — that's an accepted tradeoff, not an oversight: this
  design never uses the ULX3S's onboard ADC, so sacrificing it to gain four
  more usable GPIO is free. The diagram marks these cells with a small
  orange corner marker (legend: "ADC-shared") regardless of whether they're
  currently assigned.

The physical row order and the power/GND pin positions come from
`wiring/ulx3s-pads.json` (extracted from the ULX3S PCB with `pcbnew`, see
`extract_ulx3s_pads.py`); the diagram labels the signal pins by `gp`/`gn`
index and FPGA ball. For the exact through-hole pad numbers, or labels drawn
directly on the board photo, see `wiring/ulx3s-board-pinout.svg`.

### `wiring/rpi-header.svg` — Raspberry Pi J8 header + HIL wiring

The Raspberry Pi's 40-pin J8 GPIO header has an identical physical pinout
from the Pi B+ through the Pi 5 — on the Pi 5, RP1 exposes BCM `GPIO0`-`27`
on these same physical pins (source:
[raspberrypi.com GPIO docs](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)).
`make_pinmap.py` hard-codes this reference table (`RPI_J8_HEADER`) and draws
all 40 pins in physical order (odd column left, even column right); pins
used for hardware-in-the-loop (HIL) control are overlaid from
`wiring/pi_pinmap.csv` and coloured by domain.

- **`wiring/pi_pinmap.csv`** — the committed default Pi5 &rarr; DUT HIL
  wiring: `role,pi_signal,bcm,phys_pin,dir,connects_to,domain` per row.
  Covers SPI0 flash read-back (MOSI/MISO/SCLK/CE0), a UART0 console
  (TXD/RXD), the 6-signal JTAG bundle (TCK/TMS/TDI/TDO/TRST/SRST), and two
  spare GPIO probes. Written from a hard-coded list in `make_pinmap.py`
  (`build_pi_pinmap()`) — same pattern as `pinmap.csv`, no data duplicated
  in the SVG renderer.
- The JTAG pin defaults are verified against
  [mithro/rp1-jtag](https://github.com/mithro/rp1-jtag)'s README (NeTV2
  wiring). **rp1-jtag's pins are runtime-configurable** — this CSV records
  the committed defaults for this harness, not a hard requirement.
- `validate_pi_pinmap()` cross-checks every `(bcm, phys_pin)` pair in
  `pi_pinmap.csv` against the canonical `RPI_J8_HEADER` table and fails
  loud (`AssertionError`) on any mismatch — this is what catches a wrong Pi
  pin assignment (e.g. claiming a BCM number lives on a physical pin it
  doesn't).

Both `--headers` SVGs share the same conventions as `harness.svg`: an
explicit white background rect (theme-safe), a legend, and a fail-loud
drawn-cell-count assertion (56 = 28 `gp` + 28 `gn` for the ULX3S headers, 40
for the Pi J8) followed by re-parsing the written file with
`xml.etree.ElementTree` to confirm it's valid XML.

## Pretty pinout-library diagrams (`pinout_diagrams.py`)

`wiring/pinout_diagrams.py` renders the same data as polished connector
diagrams using the [`pinout`](https://pinout.readthedocs.io/) library
(styled labels fanning off a drawn connector body). It imports its pin data
from `make_pinmap.py` (same single source of truth) and shares
`wiring/pinout-styles.css` (embedded into each SVG, so they stay
self-contained). Regenerate with:

```sh
uv run wiring/pinout_diagrams.py
```

- **`wiring/rpi-pinout.svg`** — the Pi J8 with the committed HIL roles.
- **`wiring/ulx3s-pinout.svg`** — ULX3S J1 & J2 with KGPE-D16 assignments,
  ESP32-reserved and ADC-shared pins marked. Also embeds the ULX3S PCB **3D
  top board render** (emard/ulx3s, MIT-licensed) below the connectors, since
  no source gives the physical pin-1..40 numbers for the supply/GND rail
  cells — see `wiring/render_ulx3s_board.py` (regenerate) and
  `wiring/ULX3S-BOARD-NOTICE.md` (provenance/license).
- **`wiring/asus-headers-pinout.svg`** — each KGPE-D16 debug connector (the
  DUT side) as its own mini-header, every signal showing its ULX3S GPIO.

These are an alternative "pretty" view; the plain `harness.svg` /
`*-headers.svg` renders remain the compact reference.

## Board-image pinout (`board_pinout.py`)

`wiring/ulx3s-board-pinout.svg` is a third view of the same J1/J2 assignment,
one step more literal than `ulx3s-pinout.svg`: instead of a drawn connector
body, every label's leaderline fans off the pad's **actual physical
position** on the ULX3S 3D board render (J1 pads to the left, J2 pads to the
right), so you can match a label directly to a pad on the real board or in
the photo, not just to a schematic position. Generate/regenerate with:

```sh
uv run wiring/board_pinout.py
```

This is a two-stage pipeline, because the geometry source (`pcbnew`) isn't
`uv`-installable:

1. **`wiring/extract_ulx3s_pads.py`** — a `pcbnew` script that loads
   `ulx3s.kicad_pcb` (emard/ulx3s @6a92cec, MIT — see
   `wiring/ULX3S-BOARD-NOTICE.md`) and dumps every J1/J2 pad's number,
   mm position, and net, plus the four M3 mounting-hole positions and the
   board edge bounding box, to **`wiring/ulx3s-pads.json`** (vendored,
   committed). **`pcbnew` needs KiCad's own Python, not `uv run`** — invoke
   it with system `python3`:

   ```sh
   python3 wiring/extract_ulx3s_pads.py <path/to/ulx3s.kicad_pcb> wiring/ulx3s-pads.json
   ```

   Only re-run this if the upstream board design changes (e.g. a re-pin of
   the pinned commit); `wiring/ulx3s-pads.json` is otherwise static.

2. **`wiring/board_pinout.py`** (the `uv` script above) reads
   `wiring/ulx3s-pads.json` and `wiring/ulx3s-board-render.png` (the same
   render `ulx3s-pinout.svg` embeds — see `render_ulx3s_board.py` /
   `ULX3S-BOARD-NOTICE.md`), imports the KGPE-D16 assignment from
   `make_pinmap.py` (`build_inventory()`, `DOMAIN_BY_CONNECTOR` — the same
   single source of truth as every other diagram here), and calibrates
   mm &rarr; pixel **at runtime**: it detects the four gold mounting-hole
   blobs in the render (largest connected components, ported from the
   original scratch tool `tmp/detect_holes.py`), matches them to the four
   `holes` entries in the JSON by corner (top-left/top-right/bottom-left/
   bottom-right), and solves a least-squares affine transform from the
   matched points. It then places a curved-leaderline label at every J1/J2
   pad's projected position, colour-coded by domain, and splices in a
   title + legend, writing the fully self-contained
   `wiring/ulx3s-board-pinout.svg` (render and stylesheet both embedded).

   This fails loud (`AssertionError`) if: the mounting-hole detection
   doesn't land on a clean top-4 (a real gap between the 4th and 5th
   largest gold blob, and the top 4 similar in size to each other); the 4
   detected blobs and the 4 JSON holes don't match up to exactly the 4
   corners; the solved affine doesn't reproduce each mounting hole to
   within 5px; any J1/J2 pad projects outside the render's bounds; the
   written SVG isn't valid XML; or any of the 41 `connector.net` signals
   from `build_inventory()` is missing from the rendered labels. It also
   prints the detected hole pixel coordinates, the solved affine
   coefficients, and the pad/label counts, so a bad calibration is visible
   immediately rather than silently producing a misaligned diagram.

`wiring/ulx3s-pinout.svg` (schematic-style, above) and
`wiring/ulx3s-board-pinout.svg` (board-image, this section) are both kept —
the schematic view is easier to scan top-to-bottom by index, the board-image
view is easier to physically locate a pad on the hardware.
