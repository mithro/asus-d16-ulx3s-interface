# /// script
# requires-python = ">=3.11"
# dependencies = ["pinout"]
# ///
"""Render "pretty" hardware pinout diagrams (via the `pinout` library) for the
KGPE-D16 <-> ULX3S HIL harness.

All pin data is imported from wiring/make_pinmap.py -- the single source of
truth -- never re-typed here. This script only lays the data out visually.

Writes three self-contained SVGs into wiring/:
  - rpi-pinout.svg          Raspberry Pi J8 header
  - ulx3s-pinout.svg        ULX3S GPIO headers J1 + J2
  - asus-headers-pinout.svg KGPE-D16 debug connectors (DUT side)

Run with: uv run wiring/pinout_diagrams.py
"""
from __future__ import annotations

import base64
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WIRING = Path(__file__).resolve().parent
sys.path.insert(0, str(WIRING))

import make_pinmap as mp  # noqa: E402
from pinout.core import Group, Rect, Circle  # noqa: E402
from pinout.components.layout import Diagram  # noqa: E402
from pinout.components.pinlabel import PinLabelGroup  # noqa: E402
from pinout.components.text import TextBlock  # noqa: E402

STYLESHEET = WIRING / "pinout-styles.css"

# Legend/label colours for categories that are not a Signal "domain" (see
# make_pinmap.DOMAIN_COLOR for the SPI/UART/JTAG/GPIO ones).
NC_LABEL = "Power / GND / unused"
ESP32_LABEL = "ESP32-reserved"
ADC_LABEL = "ADC-shared"
SPARE_LABEL = "spare"
RAIL_LABEL = "supply / GND"

# ---------------------------------------------------------------------------
# ULX3S J1/J2 supply/GND rail cells + embedded PCB board render
# ---------------------------------------------------------------------------
#
# emard/ulx3s J1 and J2 are Conn_02x20_Odd_Even 40-pin headers; besides the
# gp/gn signals they each carry power/GND rails (source: the ULX3S KiCad
# design, emard/ulx3s @6a92cec, gpio.sch). No source gives the physical
# pin-1..40 numbers for these rails, so they are drawn as named rail cells
# rather than fabricated pin positions -- see wiring/ULX3S-BOARD-NOTICE.md
# and the embedded board-render panel in ulx3s-pinout.svg for the real layout.
ULX3S_RAIL_NAMES: dict[str, list[str]] = {
    "J1": ["2V5_3V3", "GND"],  # jumper-selectable 2.5V/3.3V supply + GND
    "J2": ["+5V", "+3V3", "GND"],  # +5V via STPS2L40AF diodes, +3V3, GND
}
RAIL_NOTE = "supply/GND rail — see board render below"

BOARD_RENDER_PATH = WIRING / "ulx3s-board-render.png"
BOARD_RENDER_W = 2576
BOARD_RENDER_H = 1488
BOARD_CAPTION = (
    "ULX3S PCB — 3D top render (emard/ulx3s @6a92cec, MIT). "
    "J1 (left, pins 0-13) and J2 (right, 14-27) with their supply/GND pins "
    "at each end."
)

# Rail-cell CSS is injected only into ulx3s-pinout.svg (see
# embed_board_render()), rather than added to the shared pinout-styles.css --
# that file is embedded verbatim into all three diagrams, and adding an
# ULX3S-only class there would needlessly perturb rpi-pinout.svg and
# asus-headers-pinout.svg.
RAIL_CSS = """
/* Power/GND supply-rail cells (J1/J2 connector ends) -- black fill + bold
 * gold text is a deliberately "not a signal" look, distinct from every
 * domain colour used for gp/gn signal pins. */
.rail rect.pinlabel__body {
    fill: #000000;
    stroke: #ffca28;
    stroke-width: 1.5;
}
.rail .pinlabel__text {
    fill: #ffca28;
    font-weight: bold;
}
.legend-swatch.rail {
    fill: #000000;
    stroke: #ffca28;
    stroke-width: 1.5;
}
"""


def add_connector_body(diagram: Diagram, x: float, y: float, width: float, n_holes: int,
                        row_h: float, hole_x_fracs: list[float]) -> None:
    """Draw a dark connector body of `n_holes` rows with gold pin holes at
    each fraction of `width` in `hole_x_fracs` (e.g. [0.5] for a single
    column, [0.28, 0.72] for a two-column/dual-row header)."""
    g = diagram.add(Group(x, y))
    g.add(Rect(0, 0, width, n_holes * row_h, corner_radius=4, tag="connector-body"))
    for row in range(n_holes):
        cy = row * row_h + row_h / 2
        for frac in hole_x_fracs:
            g.add(Circle(width * frac, cy, 4, tag="hole"))


def add_legend(diagram: Diagram, x: float, y: float, entries: list[tuple[str, str]]) -> None:
    """Draw a row of legend swatches. `entries` is [(label, css_class), ...]."""
    lx = x
    for label, css_class in entries:
        diagram.add(Rect(lx, y, 14, 14, corner_radius=3, tag=f"legend-swatch {css_class}"))
        diagram.add(TextBlock(label, x=lx + 20, y=y + 11, tag="legend-text"))
        lx += 20 + 8 * len(label) + 26


# ---------------------------------------------------------------------------
# (a) Raspberry Pi J8
# ---------------------------------------------------------------------------

def build_rpi_diagram() -> tuple[Diagram, list[str]]:
    pi_rows = mp.build_pi_pinmap()
    role_by_bcm = {r.bcm: r for r in pi_rows}

    ROWH = 34
    N = 20
    BOARD_W = 70
    DX = 40
    LABEL1_W = 180
    LABEL2_W = 360
    MARGIN = 60

    fan_reach = DX + LABEL1_W + LABEL2_W  # outward extent of one side's label fan
    board_x = MARGIN + fan_reach
    board_y = 140
    CANVAS_W = board_x + BOARD_W + fan_reach + MARGIN
    CANVAS_H = board_y + N * ROWH + 50

    diagram = Diagram(CANVAS_W, CANVAS_H, tag="pinout-rpi")
    diagram.add_stylesheet(str(STYLESHEET), embed=True)

    diagram.add(TextBlock(
        "Raspberry Pi J8 — HIL wiring to the ULX3S/DUT", x=30, y=40, tag="h1"))
    diagram.add(TextBlock(
        "Canonical 40-pin J8 (identical Pi B+..Pi 5). Odd physical pins fan left, "
        "even fan right. Coloured pins carry the committed HIL role from "
        "wiring/pi_pinmap.csv.", x=30, y=62, tag="h2"))

    legend_entries = [
        (label, key) for (label, _color), key in zip(mp.SIGNAL_LEGEND, mp.KEY_COLOR)
    ] + [(NC_LABEL, "nc")]
    add_legend(diagram, 30, 82, legend_entries)

    add_connector_body(diagram, board_x, board_y, BOARD_W, N, ROWH, [0.28, 0.72])

    expected_strings: list[str] = []
    left_labels: list[list[tuple]] = []
    right_labels: list[list[tuple]] = []

    for row in range(N):
        for phys, out in ((row * 2 + 1, left_labels), (row * 2 + 2, right_labels)):
            pin = mp.RPI_J8_BY_PHYS[phys]
            content1 = f"{pin.phys} {pin.label}"
            expected_strings.append(str(pin.phys))
            role = role_by_bcm.get(pin.bcm) if pin.bcm is not None else None
            if role is not None:
                tag = mp.pi_role_key(role.role)
                content2 = f"{role.role} → {role.connects_to}"
                out.append([
                    (content1, tag, {"body": {"width": LABEL1_W}}),
                    (content2, tag, {"body": {"width": LABEL2_W}}),
                ])
            else:
                out.append([(content1, "nc", {"body": {"width": LABEL1_W}})])

    diagram.add(PinLabelGroup(
        x=board_x + BOARD_W * 0.28,
        y=board_y + ROWH / 2,
        pin_pitch=(0, ROWH),
        label_start=(DX, 0),
        label_pitch=(0, ROWH),
        scale=(-1, 1),
        labels=left_labels,
    ))
    diagram.add(PinLabelGroup(
        x=board_x + BOARD_W * 0.72,
        y=board_y + ROWH / 2,
        pin_pitch=(0, ROWH),
        label_start=(DX, 0),
        label_pitch=(0, ROWH),
        scale=(1, 1),
        labels=right_labels,
    ))

    assert len(expected_strings) == 40, f"expected 40 phys pins, got {len(expected_strings)}"
    return diagram, expected_strings


# ---------------------------------------------------------------------------
# (b) ULX3S J1 / J2
# ---------------------------------------------------------------------------

def build_ulx3s_diagram() -> tuple[Diagram, list[str], float]:
    """Build the ULX3S J1/J2 diagram, sized to also hold an embedded PCB
    board-render panel below the connectors.

    Returns (diagram, expected_gp_gn_strings, panel_y) -- the third value is
    the y-coordinate reserved for the embedded board-render panel (see
    embed_board_render()), computed here so the Diagram can be constructed at
    its final, full height up front.
    """
    signals = mp.build_inventory()
    mp.assign_gpios(signals)
    mp.validate(signals)
    gpio_to_signal = {s.gpio: s for s in signals if s.gpio is not None}

    ROWH = 34
    ROWS = 14
    BOARD_W = 46
    DX = 30
    LABEL1_W = 150
    LABEL2_W = 210
    BLOCK_GAP = 90
    board_y = 170

    # Extra headroom above `board_y` so the tallest rail block (J2: +5V/+3V3/
    # GND, 3 rows) fits between the header text and the signal rows without
    # overlapping either; rail rows are then bottom-aligned to `board_y` so
    # each block's rail cells sit flush against its gp0/gn0 row.
    max_rail_rows = max(len(v) for v in ULX3S_RAIL_NAMES.values())
    board_y += max_rail_rows * ROWH

    block_half_span = DX + LABEL1_W + LABEL2_W  # outward reach of one side's fan
    block_span = 2 * block_half_span + BOARD_W
    CANVAS_W = 2 * block_span + BLOCK_GAP + 60
    # Height of the pinout content alone (connectors + rail cells), before
    # the embedded board-render panel.
    content_h = board_y + ROWS * ROWH + max_rail_rows * ROWH + 50

    # Reserve room below the pinout content for the embedded PCB board-render
    # panel: a caption line, then the panel itself sized to the render's own
    # 2576 x 1488 aspect ratio at (canvas width - margins).
    RENDER_GAP = 30
    CAPTION_H = 20
    PANEL_MARGIN = 30
    panel_w = CANVAS_W - 2 * PANEL_MARGIN
    panel_h = panel_w * (BOARD_RENDER_H / BOARD_RENDER_W)
    panel_y = content_h + RENDER_GAP + CAPTION_H
    CANVAS_H = panel_y + panel_h + PANEL_MARGIN

    diagram = Diagram(CANVAS_W, CANVAS_H, tag="pinout-ulx3s")
    diagram.add_stylesheet(str(STYLESHEET), embed=True)

    diagram.add(TextBlock(
        "ULX3S GPIO headers J1/J2 — KGPE-D16 assignment", x=30, y=40, tag="h1"))
    diagram.add(TextBlock(
        "J1 = gp/gn idx 0-13 (ASpeed/BMC side), J2 = gp/gn idx 14-27 (host/other side). "
        "gp fans left, gn fans right. idx 11-13 reserved for the on-board ESP32; "
        "idx 14-17 double as the onboard ADC (AIN0-7). Each header also carries "
        "power/GND rails at both physical ends (2V5_3V3/GND on J1; +5V/+3V3/GND "
        "on J2) -- no source gives their physical pin numbers, so see the "
        "embedded board render below for the real layout.", x=30, y=62, tag="h2"))

    legend_entries = (
        [(label, key) for (label, _color), key in zip(mp.SIGNAL_LEGEND, mp.KEY_COLOR)]
        + [(SPARE_LABEL, "spare"), (ESP32_LABEL, "esp32"), (ADC_LABEL, "adc"), (RAIL_LABEL, "rail")]
    )
    add_legend(diagram, 30, 82, legend_entries)

    expected_strings: list[str] = []

    def pin_labels(which: str, idx: int) -> list[tuple]:
        pin = mp.ULX3S_PIN_BY_IDX[idx]
        ball = pin.gp_ball if which == "gp" else pin.gn_ball
        name = f"{which}{idx}"
        expected_strings.append(name)
        adc_note = " (ADC)" if idx in mp.ULX3S_ADC_SHARED_IDX else ""
        content1 = f"{name} ({ball}){adc_note}"
        adc_tag = " adc" if idx in mp.ULX3S_ADC_SHARED_IDX else ""

        if idx in mp.ULX3S_ESP32_IDX:
            return [(content1, f"esp32{adc_tag}", {"body": {"width": LABEL1_W}})]

        sig = gpio_to_signal.get(name)
        if sig is not None:
            key_tag = mp.connector_key(sig.connector)
            content2 = f"{sig.connector}.{sig.net}"
            return [
                (content1, f"{key_tag}{adc_tag}", {"body": {"width": LABEL1_W}}),
                (content2, f"{key_tag}{adc_tag}", {"body": {"width": LABEL2_W}}),
            ]

        return [(content1, f"spare{adc_tag}", {"body": {"width": LABEL1_W}})]

    def rail_labels(names: list[str]) -> list[list[tuple]]:
        # One wide cell (not the usual name+note two-cell chain) -- the note
        # text is too long to fit LABEL2_W's normal signal-name width.
        return [
            [(f"{name} — {RAIL_NOTE}", "rail", {"body": {"width": LABEL1_W + LABEL2_W}})]
            for name in names
        ]

    def add_rail_fans(x: float, y: float, names: list[str]) -> None:
        """Draw `names` as a rail-styled extension of the connector body at
        `x, y` (top-left corner), fanned identically to both the gp (left)
        and gn (right) sides -- rails apply to the whole connector, not to
        one signal column."""
        n = len(names)
        add_connector_body(diagram, x, y, BOARD_W, n, ROWH, [0.5])
        labels = rail_labels(names)
        for scale in (-1, 1):
            diagram.add(PinLabelGroup(
                x=x + BOARD_W * 0.5,
                y=y + ROWH / 2,
                pin_pitch=(0, ROWH),
                label_start=(DX, 0),
                label_pitch=(0, ROWH),
                scale=(scale, 1),
                labels=labels,
            ))

    block_x = 30 + block_half_span
    for block_name, idx_range in (("J1", mp.ULX3S_J1_IDX), ("J2", mp.ULX3S_J2_IDX)):
        rails = ULX3S_RAIL_NAMES[block_name]
        y_top_rail = board_y - len(rails) * ROWH
        y_bottom_rail = board_y + ROWS * ROWH

        diagram.add(TextBlock(block_name, x=block_x + BOARD_W / 2 - 8, y=y_top_rail - 14, tag="h1"))

        add_rail_fans(block_x, y_top_rail, rails)
        add_connector_body(diagram, block_x, board_y, BOARD_W, ROWS, ROWH, [0.5])
        add_rail_fans(block_x, y_bottom_rail, rails)

        left_labels = [pin_labels("gp", idx) for idx in idx_range]
        right_labels = [pin_labels("gn", idx) for idx in idx_range]

        diagram.add(PinLabelGroup(
            x=block_x + BOARD_W * 0.5,
            y=board_y + ROWH / 2,
            pin_pitch=(0, ROWH),
            label_start=(DX, 0),
            label_pitch=(0, ROWH),
            scale=(-1, 1),
            labels=left_labels,
        ))
        diagram.add(PinLabelGroup(
            x=block_x + BOARD_W * 0.5,
            y=board_y + ROWH / 2,
            pin_pitch=(0, ROWH),
            label_start=(DX, 0),
            label_pitch=(0, ROWH),
            scale=(1, 1),
            labels=right_labels,
        ))

        block_x += block_span + BLOCK_GAP

    assert len(expected_strings) == 56, f"expected 56 gp/gn names, got {len(expected_strings)}"
    return diagram, expected_strings, panel_y


def embed_board_render(svg_text: str, canvas_w: float, panel_y: float) -> tuple[str, float]:
    """Splice the vendored ULX3S PCB board-render PNG into `svg_text` as a
    self-contained base64-encoded <image> element, plus a caption, inserted
    just before the closing </svg>.

    Returns (new_svg_text, panel_h).
    """
    png_bytes = BOARD_RENDER_PATH.read_bytes()
    b64 = base64.b64encode(png_bytes).decode("ascii")

    PANEL_MARGIN = 30
    panel_x = PANEL_MARGIN
    panel_w = canvas_w - 2 * PANEL_MARGIN
    panel_h = panel_w * (BOARD_RENDER_H / BOARD_RENDER_W)

    image_el = (
        f'<image x="{panel_x}" y="{panel_y}" width="{panel_w:.4f}" '
        f'height="{panel_h:.4f}" preserveAspectRatio="xMidYMid meet" '
        f'href="data:image/png;base64,{b64}"/>'
    )

    caption_y = panel_y - 10
    caption = f'<text x="{panel_x}" y="{caption_y}" class="h2">{BOARD_CAPTION}</text>'

    # Rail-cell CSS as its own <style> block (see RAIL_CSS's docstring for
    # why this isn't added to the shared pinout-styles.css instead).
    rail_style = f'<style type="text/css" media="screen"><![CDATA[{RAIL_CSS}]]></style>'

    insert_at = svg_text.rindex("</svg>")
    new_svg_text = (
        svg_text[:insert_at] + rail_style + image_el + caption + svg_text[insert_at:]
    )

    return new_svg_text, panel_h


# ---------------------------------------------------------------------------
# (c) KGPE-D16 debug connectors (DUT side)
# ---------------------------------------------------------------------------

def build_asus_diagram() -> tuple[Diagram, list[str]]:
    signals = mp.build_inventory()
    mp.assign_gpios(signals)
    mp.validate(signals)

    groups: list[tuple[str, list]] = []
    for s in signals:
        if groups and groups[-1][0] == s.connector:
            groups[-1][1].append(s)
        else:
            groups.append((s.connector, [s]))

    N_COLS = 3
    N_ROWS = -(-len(groups) // N_COLS)  # ceil
    ROWH = 26
    MAX_ROWS = max(len(sigs) for _, sigs in groups)
    BOARD_W = 26
    DX = 40
    LABEL_W = 260

    CELL_W = BOARD_W + DX + LABEL_W + 50
    CELL_H = 30 + MAX_ROWS * ROWH + 20
    COL_GAP = 40
    ROW_GAP = 40
    TOP = 165

    CANVAS_W = 60 + N_COLS * CELL_W + (N_COLS - 1) * COL_GAP
    CANVAS_H = TOP + N_ROWS * CELL_H + (N_ROWS - 1) * ROW_GAP + 40

    diagram = Diagram(CANVAS_W, CANVAS_H, tag="pinout-asus")
    diagram.add_stylesheet(str(STYLESHEET), embed=True)

    diagram.add(TextBlock(
        "KGPE-D16 debug connectors — signal → ULX3S GPIO", x=30, y=40, tag="h1"))
    diagram.add(TextBlock(
        "Each box is one physical connector on the KGPE-D16/host side of the harness. "
        "Every signal shows the ULX3S GPIO it is wired to (see wiring/pinmap.csv).",
        x=30, y=62, tag="h2"))
    diagram.add(TextBlock(
        "This is the DUT side of the harness -- compare with ulx3s-pinout.svg.",
        x=30, y=76, tag="h2"))

    legend_entries = [(label, key) for (label, _color), key in zip(mp.SIGNAL_LEGEND, mp.KEY_COLOR)]
    add_legend(diagram, 30, 96, legend_entries)

    expected_strings: list[str] = []

    for i, (connector, sigs) in enumerate(groups):
        col = i % N_COLS
        row = i // N_COLS
        cell_x = 30 + col * (CELL_W + COL_GAP)
        cell_y = TOP + row * (CELL_H + ROW_GAP)

        key_tag = mp.connector_key(connector)
        diagram.add(TextBlock(
            connector, x=cell_x, y=cell_y + 12, tag=f"connector-title {key_tag}-text"))

        board_y = cell_y + 30
        add_connector_body(diagram, cell_x, board_y, BOARD_W, len(sigs), ROWH, [0.5])

        row_labels = []
        for s in sigs:
            content = f"{s.connector}.{s.net} → {s.gpio}"
            expected_strings.append(f"{s.connector}.{s.net}")
            row_labels.append([(content, key_tag, {"body": {"width": LABEL_W}})])

        diagram.add(PinLabelGroup(
            x=cell_x + BOARD_W * 0.5,
            y=board_y + ROWH / 2,
            pin_pitch=(0, ROWH),
            label_start=(DX, 0),
            label_pitch=(0, ROWH),
            scale=(1, 1),
            labels=row_labels,
        ))

    assert len(expected_strings) == 41, f"expected 41 connector.net signals, got {len(expected_strings)}"
    return diagram, expected_strings


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def render_and_verify(name: str, build_fn) -> None:
    diagram, expected_strings = build_fn()
    svg_text = diagram.render()
    out_path = WIRING / name
    out_path.write_text(svg_text)

    # Fail loud if the written file isn't valid XML.
    root = ET.parse(out_path).getroot()
    width = root.get("width")
    height = root.get("height")

    missing = [s for s in expected_strings if s not in svg_text]
    assert not missing, f"{name}: missing expected content: {missing}"

    print(f"wrote {out_path}  ({width} x {height})  "
          f"{len(expected_strings)} expected strings present")


def render_ulx3s_and_verify() -> None:
    """Like render_and_verify(), but for ulx3s-pinout.svg specifically: after
    rendering the pinout diagram, splices in the embedded PCB board-render
    panel (see embed_board_render()) before writing and verifying the file.
    """
    name = "ulx3s-pinout.svg"
    diagram, expected_strings, panel_y = build_ulx3s_diagram()
    svg_text = diagram.render()
    svg_text, panel_h = embed_board_render(svg_text, float(diagram.width), panel_y)

    out_path = WIRING / name
    out_path.write_text(svg_text, encoding="utf-8")

    # Fail loud if the written file isn't valid XML (the embedded <image>
    # must keep the document well-formed).
    root = ET.parse(out_path).getroot()
    width = root.get("width")
    height = root.get("height")

    all_expected = expected_strings + [BOARD_CAPTION, "data:image/png;base64,"]
    missing = [s for s in all_expected if s not in svg_text]
    assert not missing, f"{name}: missing expected content: {missing}"

    print(f"wrote {out_path}  ({width} x {height})  board render panel height {panel_h:.1f}  "
          f"{len(expected_strings)} gp/gn strings + board render + caption present")


def main() -> None:
    render_and_verify("rpi-pinout.svg", build_rpi_diagram)
    render_ulx3s_and_verify()
    render_and_verify("asus-headers-pinout.svg", build_asus_diagram)


if __name__ == "__main__":
    main()
