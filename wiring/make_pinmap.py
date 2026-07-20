# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Generate and validate the KGPE-D16 connector -> ULX3S GPIO pin map.

The FPGA terminates every debug connector through one fixed cable harness,
so every signal in the inventory below must get exactly one GPIO,
concurrently (no re-cabling between roles). This script:

  1. Defines the signal inventory as structured data.
  2. Defines the ULX3S GPIO pool (50 usable pins after ESP32 reservations).
  3. Assigns every non-fixed signal a free GPIO, deterministically.
  4. Validates the whole map (validate()).
  5. Prints a per-connector summary and writes wiring/pinmap.csv.

Run with: uv run wiring/make_pinmap.py
Run with: uv run wiring/make_pinmap.py --svg   (also renders wiring/harness.svg)
"""

from __future__ import annotations

import argparse
import csv
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape


@dataclass
class Signal:
    connector: str
    net: str
    dir: str  # "in" = FPGA input, "out" = FPGA output
    via: str  # "direct", "MAX3232", "1.27mm-adapter"
    gpio: str | None = None  # e.g. "gp7"; None until assigned


# ---------------------------------------------------------------------------
# GPIO pool
# ---------------------------------------------------------------------------

# ULX3S: 56 GPIO named gp[0..27] and gn[0..27].
ALL_GPIO: list[str] = [f"gp{i}" for i in range(28)] + [f"gn{i}" for i in range(28)]

# Shared with the on-board ESP32 -- do not use for KGPE-D16 wiring.
ESP32_RESERVED: set[str] = {"gp11", "gp12", "gp13", "gn11", "gn12", "gn13"}

# The 50 usable GPIO, in deterministic assignment order: gp[0..27] then
# gn[0..27], skipping the ESP32-reserved pins.
USABLE_GPIO: list[str] = [p for p in ALL_GPIO if p not in ESP32_RESERVED]

assert len(USABLE_GPIO) == 50, f"expected 50 usable GPIO, got {len(USABLE_GPIO)}"


# ---------------------------------------------------------------------------
# Signal inventory
# ---------------------------------------------------------------------------

def build_inventory() -> list[Signal]:
    sig = Signal
    return [
        # BMC_FW1 -- BMC SPI flash R/W + straps, 3V3, via=direct.
        # CS0/SCK/MOSI/MISO MUST stay spispy-cable-compatible.
        sig("BMC_FW1", "CS0", "in", "direct", gpio="gp7"),
        sig("BMC_FW1", "SCK", "in", "direct", gpio="gp8"),
        sig("BMC_FW1", "MOSI/SPIDO", "in", "direct", gpio="gp9"),
        sig("BMC_FW1", "MISO/SPIDI", "out", "direct", gpio="gp10"),
        sig("BMC_FW1", "CS2", "in", "direct"),
        sig("BMC_FW1", "IKVMEN#", "out", "direct"),  # strap
        sig("BMC_FW1", "BMC_PRESENT#", "out", "direct"),  # strap
        sig("BMC_FW1", "SOLEN#", "out", "direct"),  # strap

        # FU1 -- host BIOS SPI flash, read-only, 3V3, via=direct.
        sig("FU1", "CS#", "in", "direct"),
        sig("FU1", "CLK", "in", "direct"),
        sig("FU1", "MOSI/DI", "in", "direct"),
        sig("FU1", "MISO/DO", "out", "direct"),
        sig("FU1", "HOLD#", "in", "direct"),

        # AST_UART1 -- BMC console, 3V3 TTL, via=direct.
        sig("AST_UART1", "BMC_RXD", "out", "direct"),  # FPGA drives BMC's RX
        sig("AST_UART1", "BMC_TXD", "in", "direct"),  # FPGA reads BMC's TX

        # COM1/COM2 -- host serial, via=MAX3232.
        sig("COM1", "COM1_TX", "out", "MAX3232"),
        sig("COM1", "COM1_RX", "in", "MAX3232"),
        sig("COM2", "COM2_TX", "out", "MAX3232"),
        sig("COM2", "COM2_RX", "in", "MAX3232"),

        # AST_JTAG1 -- BMC ARM TAP, 3V3, via=direct. FPGA is JTAG master.
        sig("AST_JTAG1", "TCK", "out", "direct"),
        sig("AST_JTAG1", "TMS", "out", "direct"),
        sig("AST_JTAG1", "TDI", "out", "direct"),
        sig("AST_JTAG1", "TDO", "in", "direct"),
        sig("AST_JTAG1", "NTRST", "out", "direct"),
        sig("AST_JTAG1", "RTCK", "in", "direct"),
        sig("AST_JTAG1", "SRST#", "out", "direct"),

        # AMD_HDT -- host CPU HDT, via=1.27mm-adapter. FPGA is JTAG master.
        sig("AMD_HDT", "HDT_TCK", "out", "1.27mm-adapter"),
        sig("AMD_HDT", "HDT_TMS", "out", "1.27mm-adapter"),
        sig("AMD_HDT", "HDT_TDI", "out", "1.27mm-adapter"),
        sig("AMD_HDT", "HDT_TDO", "in", "1.27mm-adapter"),
        sig("AMD_HDT", "HDT_TRST_L", "out", "1.27mm-adapter"),

        # PANEL1 -- front panel, via=direct.
        sig("PANEL1", "PWRBTN#", "out", "direct"),  # open-drain
        sig("PANEL1", "RESET#", "out", "direct"),  # open-drain
        sig("PANEL1", "NMIBNT#", "out", "direct"),  # open-drain
        sig("PANEL1", "PLED", "in", "direct"),
        sig("PANEL1", "HDLED", "in", "direct"),
        sig("PANEL1", "MLED", "in", "direct"),

        # JUMPERS -- drive to change state, via=direct.
        sig("JUMPERS", "VGA_SW1", "out", "direct"),
        sig("JUMPERS", "IPMI_SEL", "out", "direct"),
        sig("JUMPERS", "BIOS_RECOVERY#", "out", "direct"),
        sig("JUMPERS", "CLRTC", "out", "direct"),
    ]


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def assign_gpios(signals: list[Signal]) -> None:
    """Assign a free GPIO to every signal that doesn't already have a fixed one.

    Deterministic order: walk USABLE_GPIO in pool order (gp[0..27] then
    gn[0..27], reserved already excluded), skipping pins already taken by a
    fixed signal, and hand them out to the remaining signals in inventory
    order.
    """
    already_used = {s.gpio for s in signals if s.gpio is not None}
    free_pins = iter(p for p in USABLE_GPIO if p not in already_used)

    for s in signals:
        if s.gpio is None:
            s.gpio = next(free_pins)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(signals: list[Signal]) -> None:
    # (b) total signals <= usable GPIO pool.
    assert len(signals) <= len(USABLE_GPIO), (
        f"{len(signals)} signals exceeds the {len(USABLE_GPIO)}-pin usable GPIO pool"
    )

    # (c) every signal has a GPIO assigned.
    unassigned = [f"{s.connector}.{s.net}" for s in signals if s.gpio is None]
    assert not unassigned, f"unassigned signals (no GPIO): {unassigned}"

    # (a) no GPIO assigned to two signals.
    used_gpios = [s.gpio for s in signals]
    dupes = {g for g in used_gpios if used_gpios.count(g) > 1}
    assert not dupes, f"GPIO(s) assigned to more than one signal: {sorted(dupes)}"

    # No signal uses a reserved (ESP32) pin.
    reserved_hits = [
        f"{s.connector}.{s.net}={s.gpio}" for s in signals if s.gpio in ESP32_RESERVED
    ]
    assert not reserved_hits, f"signal(s) assigned to ESP32-reserved pins: {reserved_hits}"

    # (d) the four BMC_FW1 fixed pins are exactly gp7/gp8/gp9/gp10.
    fixed_expected = {
        "CS0": "gp7",
        "SCK": "gp8",
        "MOSI/SPIDO": "gp9",
        "MISO/SPIDI": "gp10",
    }
    bmc_fw1 = {s.net: s.gpio for s in signals if s.connector == "BMC_FW1"}
    for net, expected_gpio in fixed_expected.items():
        assert bmc_fw1.get(net) == expected_gpio, (
            f"BMC_FW1 {net} must be fixed at {expected_gpio}, got {bmc_fw1.get(net)}"
        )

    # (e) RS-232 signals go through MAX3232; HDT signals go through the
    # 1.27mm adapter.
    for s in signals:
        if s.connector in ("COM1", "COM2"):
            assert s.via == "MAX3232", (
                f"{s.connector}.{s.net} must have via=='MAX3232', got {s.via!r}"
            )
        if s.connector == "AMD_HDT":
            assert s.via == "1.27mm-adapter", (
                f"{s.connector}.{s.net} must have via=='1.27mm-adapter', got {s.via!r}"
            )


# ---------------------------------------------------------------------------
# Reporting / output
# ---------------------------------------------------------------------------

def print_summary(signals: list[Signal]) -> None:
    connectors: dict[str, list[Signal]] = {}
    for s in signals:
        connectors.setdefault(s.connector, []).append(s)

    for connector, sigs in connectors.items():
        print(f"\n{connector} ({sigs[0].via if len(set(x.via for x in sigs)) == 1 else 'mixed'}):")
        for s in sigs:
            print(f"  {s.net:<16} {s.dir:<3} {s.via:<16} {s.gpio}")

    used = sum(1 for s in signals if s.gpio is not None)
    print(f"\nused {used} / {len(USABLE_GPIO)} GPIO")


def write_csv(signals: list[Signal], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["connector", "net", "dir", "via", "gpio"])
        for s in signals:
            writer.writerow([s.connector, s.net, s.dir, s.via, s.gpio])


# ---------------------------------------------------------------------------
# SVG harness diagram
# ---------------------------------------------------------------------------

# Which signal domain each connector belongs to, for colour-coding.
DOMAIN_BY_CONNECTOR: dict[str, str] = {
    "BMC_FW1": "SPI",
    "FU1": "SPI",
    "AST_UART1": "UART",
    "COM1": "UART",
    "COM2": "UART",
    "AST_JTAG1": "JTAG",
    "AMD_HDT": "JTAG",
    "PANEL1": "GPIO",
    "JUMPERS": "GPIO",
}

# Colours chosen for good contrast against a white background (see BG_COLOR).
DOMAIN_COLOR: dict[str, str] = {
    "SPI": "#1b5e20",  # dark green
    "UART": "#0d47a1",  # dark blue
    "JTAG": "#b71c1c",  # dark red
    "GPIO": "#4a148c",  # dark purple
}

BG_COLOR = "#ffffff"
TEXT_COLOR = "#111111"
LINE_COLOR = "#444444"


def render_svg(signals: list[Signal], path: Path) -> None:
    """Render a three-column harness diagram derived from `signals`.

    Columns: KGPE-D16 connector pin (left) -- harness element / via (middle)
    -- ULX3S GPIO (right). Rows are grouped by connector, colour-coded by
    signal domain, and carry an arrowhead showing FPGA-relative direction.
    """
    MARGIN = 20
    ROW_H = 20
    ROW_SPACING = 24
    GROUP_GAP = 14
    GROUP_LABEL_H = 18

    LEFT_X = MARGIN
    LEFT_W = 230
    MID_X = LEFT_X + LEFT_W + 80
    MID_W = 150
    RIGHT_X = MID_X + MID_W + 80
    RIGHT_W = 70
    CANVAS_W = RIGHT_X + RIGHT_W + MARGIN

    TITLE_H = 30
    LEGEND_Y = MARGIN + TITLE_H + 10
    LEGEND_H = 24
    ROWS_TOP = LEGEND_Y + LEGEND_H + 20

    # Group signals by connector, preserving inventory order.
    groups: list[tuple[str, list[Signal]]] = []
    for s in signals:
        if groups and groups[-1][0] == s.connector:
            groups[-1][1].append(s)
        else:
            groups.append((s.connector, [s]))

    n_groups = len(groups)
    n_rows = len(signals)
    canvas_h = (
        ROWS_TOP
        + n_groups * GROUP_LABEL_H
        + n_rows * ROW_SPACING
        + (n_groups - 1) * GROUP_GAP
        + MARGIN
    )

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{canvas_h}" '
        f'viewBox="0 0 {CANVAS_W} {canvas_h}" font-family="monospace" font-size="11">'
    )
    # Explicit background so the diagram is legible regardless of the
    # viewer's page background (light or dark theme).
    parts.append(f'<rect x="0" y="0" width="{CANVAS_W}" height="{canvas_h}" fill="{BG_COLOR}"/>')

    parts.append(
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE_COLOR}"/>'
        '</marker>'
        '</defs>'
    )

    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 16}" font-size="16" font-weight="bold" fill="{TEXT_COLOR}">'
        'KGPE-D16 to ULX3S bench-controller wiring harness</text>'
    )
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 16 + 14}" font-size="10" fill="{TEXT_COLOR}">'
        'One fixed harness, all connectors terminated concurrently. ULX3S J1/J2 GPIO headers '
        '(gp0-27, gn0-27; gp/gn11-13 reserved for the on-board ESP32). Arrows point FPGA-relative '
        '(in = connector to GPIO, out = GPIO to connector).</text>'
    )

    legend_x = MARGIN
    for domain, color in DOMAIN_COLOR.items():
        parts.append(f'<rect x="{legend_x}" y="{LEGEND_Y}" width="12" height="12" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x + 16}" y="{LEGEND_Y + 10}" fill="{TEXT_COLOR}">{escape(domain)}</text>'
        )
        legend_x += 90

    col_header_y = ROWS_TOP - 8
    parts.append(
        f'<text x="{LEFT_X}" y="{col_header_y}" fill="{TEXT_COLOR}" font-weight="bold">'
        'KGPE-D16 connector pin</text>'
    )
    parts.append(
        f'<text x="{MID_X}" y="{col_header_y}" fill="{TEXT_COLOR}" font-weight="bold">'
        'harness element</text>'
    )
    parts.append(
        f'<text x="{RIGHT_X}" y="{col_header_y}" fill="{TEXT_COLOR}" font-weight="bold">'
        'ULX3S GPIO</text>'
    )

    rect_count = 0
    y = ROWS_TOP
    for gi, (connector, group_signals) in enumerate(groups):
        domain = DOMAIN_BY_CONNECTOR[connector]
        color = DOMAIN_COLOR[domain]
        parts.append(
            f'<text x="{LEFT_X}" y="{y + GROUP_LABEL_H - 6}" fill="{color}" font-weight="bold">'
            f'{escape(connector)}</text>'
        )
        y += GROUP_LABEL_H

        for s in group_signals:
            row_cy = y + ROW_H / 2

            left_edge = LEFT_X + LEFT_W
            mid_left_edge = MID_X
            mid_right_edge = MID_X + MID_W
            right_edge = RIGHT_X

            # left box: connector.net
            parts.append(
                f'<rect class="signal-row" x="{LEFT_X}" y="{y}" width="{LEFT_W}" height="{ROW_H}" '
                f'fill="none" stroke="{color}" stroke-width="1.5" rx="3"/>'
            )
            rect_count += 1
            label = f"{s.connector}.{s.net}"
            parts.append(
                f'<text x="{LEFT_X + 6}" y="{row_cy + 4}" fill="{TEXT_COLOR}">{escape(label)}</text>'
            )

            # middle box: via (harness element)
            parts.append(
                f'<rect x="{MID_X}" y="{y}" width="{MID_W}" height="{ROW_H}" '
                f'fill="none" stroke="{color}" stroke-width="1.5" rx="3"/>'
            )
            parts.append(
                f'<text x="{MID_X + 6}" y="{row_cy + 4}" fill="{TEXT_COLOR}">{escape(s.via)}</text>'
            )

            # right box: ULX3S GPIO
            parts.append(
                f'<rect x="{RIGHT_X}" y="{y}" width="{RIGHT_W}" height="{ROW_H}" '
                f'fill="none" stroke="{color}" stroke-width="1.5" rx="3"/>'
            )
            parts.append(
                f'<text x="{RIGHT_X + 6}" y="{row_cy + 4}" fill="{TEXT_COLOR}">'
                f'{escape(s.gpio or "?")}</text>'
            )

            # Connecting lines, arrowhead pointing toward the FPGA-relative
            # destination: dir=="in" flows connector -> harness -> GPIO;
            # dir=="out" flows GPIO -> harness -> connector.
            if s.dir == "in":
                seg1 = (left_edge, row_cy, mid_left_edge, row_cy)
                seg2 = (mid_right_edge, row_cy, right_edge, row_cy)
            else:
                seg1 = (mid_left_edge, row_cy, left_edge, row_cy)
                seg2 = (right_edge, row_cy, mid_right_edge, row_cy)

            parts.append(
                f'<line x1="{seg1[0]}" y1="{seg1[1]}" x2="{seg1[2]}" y2="{seg1[3]}" '
                f'stroke="{LINE_COLOR}" stroke-width="1.5" marker-end="url(#arrow)"/>'
            )
            parts.append(
                f'<line x1="{seg2[0]}" y1="{seg2[1]}" x2="{seg2[2]}" y2="{seg2[3]}" '
                f'stroke="{LINE_COLOR}" stroke-width="1.5" marker-end="url(#arrow)"/>'
            )

            y += ROW_SPACING

        if gi != n_groups - 1:
            y += GROUP_GAP

    parts.append('</svg>')
    svg_text = "\n".join(parts)

    # (a) every signal drew exactly one row rect -- fail loud if not.
    assert rect_count == len(signals), (
        f"SVG row-rect count {rect_count} != signal count {len(signals)}"
    )
    print(f"SVG rows drawn: {rect_count}, signals: {len(signals)}")

    path.write_text(svg_text)

    # (b) the written file must be valid XML.
    ET.parse(path)

    # (c) every connector.net label must appear in the SVG text.
    missing = [
        f"{s.connector}.{s.net}" for s in signals if f"{s.connector}.{s.net}" not in svg_text
    ]
    assert not missing, f"signal label(s) missing from SVG: {missing}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--svg",
        action="store_true",
        help="also render wiring/harness.svg from the same in-memory signal list",
    )
    args = parser.parse_args()

    signals = build_inventory()
    assign_gpios(signals)
    validate(signals)
    print_summary(signals)

    out_path = Path(__file__).parent / "pinmap.csv"
    write_csv(signals, out_path)
    print(f"\nwrote {out_path}")

    if args.svg:
        svg_path = Path(__file__).parent / "harness.svg"
        render_svg(signals, svg_path)
        print(f"wrote {svg_path}")


if __name__ == "__main__":
    main()
