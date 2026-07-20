# Wiring: KGPE-D16 &harr; ULX3S bench-controller harness

## One fixed harness

The ULX3S terminates **every** debug/control connector on the KGPE-D16 at
once, through a single cable harness that stays plugged in permanently.
There is no re-cabling to switch between roles (e.g. "SPI mode" vs "JTAG
mode") — all 41 signals across all 9 connectors are wired to their own GPIO
concurrently. This is what `wiring/make_pinmap.py` encodes and validates: it
builds the full 41-signal inventory, assigns each one a free ULX3S GPIO
(50 usable pins after excluding the 6 reserved for the on-board ESP32), and
asserts the map is injective, complete, and satisfies the fixed constraints
below before ever writing output.

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
  (`CS0`, `SCK`, `MOSI/SPIDO`, `MISO/SPIDI`) are pinned to `gp7`/`gp8`/`gp9`/
  `gp10` specifically (rather than auto-assigned like the rest of the map)
  so the harness stays compatible with existing spispy pogo/cable pinouts —
  enforced by `validate()`'s fixed-pin assertion.
- **Everything else &rarr; direct.** All other signals (BMC_FW1 straps, FU1
  host-BIOS SPI, AST_UART1, AST_JTAG1, PANEL1, JUMPERS) are native 3V3 and
  wire straight to a GPIO with no level shifting or adapter.

## Regenerating

```sh
uv run wiring/make_pinmap.py           # validate + print summary + write pinmap.csv
uv run wiring/make_pinmap.py --svg     # same, plus render wiring/harness.svg
```

Both invocations fail loud (`AssertionError`) if the map is invalid — e.g. a
duplicate GPIO, an unassigned signal, a reserved-pin collision, a fixed
BMC_FW1 pin drifting off `gp7`-`gp10`, or a `COM1`/`COM2`/`AMD_HDT` signal
missing its required `via`. The `--svg` path additionally asserts the drawn
row count equals the signal count (41), re-parses the written file as XML,
and confirms every `connector.net` label from `pinmap.csv` appears in the
rendered SVG text.
