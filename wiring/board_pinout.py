# /// script
# requires-python = ">=3.11"
# dependencies = ["pinout", "numpy", "scipy", "pillow"]
# ///
"""Board-image pinout: labels fanning off each physical J1/J2 pad on the
ULX3S 3D board render (emard/ulx3s @6a92cec, MIT).

Unlike `wiring/pinout_diagrams.py` (which draws a schematic-style header
block), this diagram places each label at the pad's *actual* pixel position
on the rendered PCB image, with a curved pinout leaderline fanning out to a
tidy column. The render carries no embedded coordinate metadata, so
calibration is done at runtime:

  1. crop `wiring/ulx3s-board-render.png` to the board (the non-transparent
     bounding box) so the board fills the frame;
  2. detect the four M3 mounting holes as the four largest gold blobs in the
     cropped image, match them by corner to the holes' known mm positions in
     `wiring/ulx3s-pads.json`, and solve a least-squares mm -> pixel affine;
  3. project every pad's mm position through that affine and drop a label
     there, fanning J1 left / J2 right, ordered top-to-bottom by pad Y.

Left/right labels use a single-pin `PinLabelGroup` (scale=(-1,1) / (1,1)) so
the leaderline attaches to the board-facing edge and the text is not mirrored.

Run with: uv run wiring/board_pinout.py
Regenerate `wiring/ulx3s-pads.json` first with `extract_ulx3s_pads.py`
(system python3 + pcbnew) if the upstream board design changes.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image as PILImage
from scipy import ndimage

WIRING = Path(__file__).resolve().parent
sys.path.insert(0, str(WIRING))
import make_pinmap as mp  # noqa: E402

from pinout.core import Image  # noqa: E402
from pinout.components.layout import Diagram  # noqa: E402
from pinout.components.pinlabel import PinLabelGroup  # noqa: E402

PADS_JSON = WIRING / "ulx3s-pads.json"
BOARD_PNG = WIRING / "ulx3s-board-render.png"
OUT_SVG = WIRING / "ulx3s-board-pinout.svg"

pads = json.loads(PADS_JSON.read_text())

# --- 1. load the render and crop to the board (non-transparent) bbox -----
src = PILImage.open(BOARD_PNG).convert("RGBA")
alpha = np.asarray(src)[..., 3]
ys, xs = np.where(alpha > 20)
assert xs.size, "render appears fully transparent"
PADX = 12
x0, x1 = max(0, int(xs.min()) - PADX), min(src.width, int(xs.max()) + PADX)
y0, y1 = max(0, int(ys.min()) - PADX), min(src.height, int(ys.max()) + PADX)
crop = src.crop((x0, y0, x1, y1))
CW, CH = crop.size
crop_arr = np.asarray(crop)
print(f"cropped board to {CW}x{CH} (from {src.width}x{src.height})")


# --- 2. detect the 4 mounting-hole gold blobs at runtime -----------------
def detect_mounting_holes(arr: np.ndarray) -> list[dict]:
    r, g, b, al = (arr[..., i].astype(int) for i in range(4))
    gold = (al > 180) & (r > 150) & (g > 115) & (b < 130) & (r - b > 60) & (g - b > 35)
    lbl, n = ndimage.label(gold)
    assert n >= 4, f"only found {n} gold connected component(s), need >= 4 mounting holes"
    areas = ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))
    cents = ndimage.center_of_mass(gold, lbl, range(1, n + 1))  # (row=y, col=x)
    comps = sorted(
        [{"area": float(areas[i]), "x": float(cents[i][1]), "y": float(cents[i][0])}
         for i in range(n)],
        key=lambda c: -c["area"],
    )
    top4 = comps[:4]
    fifth = comps[4]["area"] if len(comps) > 4 else 0.0
    min4 = min(c["area"] for c in top4)
    max4 = max(c["area"] for c in top4)
    assert fifth < 0.7 * min4, (
        f"no clear gap between the 4th ({min4:.0f}) and 5th ({fifth:.0f}) largest gold "
        f"blobs -- mounting-hole detection is ambiguous"
    )
    assert max4 < 2.0 * min4, (
        f"the 4 largest gold blobs vary too much in size ({min4:.0f}..{max4:.0f})"
    )
    return top4


detected_holes = detect_mounting_holes(crop_arr)
print(f"detected 4 mounting holes in the cropped render:")
for h in detected_holes:
    print(f"  area={h['area']:.0f}  x={h['x']:.1f}  y={h['y']:.1f}")

# --- 3. match detected px blobs to the json's mm holes, by corner --------
json_holes = pads["holes"]
assert len(json_holes) == 4, f"expected exactly 4 holes in {PADS_JSON}, got {len(json_holes)}"


def corner_key(x: float, y: float, xs_: list[float], ys_: list[float]) -> str:
    cx_, cy_ = (min(xs_) + max(xs_)) / 2, (min(ys_) + max(ys_)) / 2
    return ("L" if x < cx_ else "R") + ("T" if y < cy_ else "B")


mm_xs = [h["x"] for h in json_holes]
mm_ys = [h["y"] for h in json_holes]
mm_by_corner = {corner_key(h["x"], h["y"], mm_xs, mm_ys): h for h in json_holes}
px_xs = [h["x"] for h in detected_holes]
px_ys = [h["y"] for h in detected_holes]
px_by_corner = {corner_key(h["x"], h["y"], px_xs, px_ys): h for h in detected_holes}
assert set(mm_by_corner) == set(px_by_corner) == {"LT", "RT", "LB", "RB"}, (
    f"could not match 4 holes to 4 corners: mm={sorted(mm_by_corner)}, px={sorted(px_by_corner)}"
)

# --- 4. solve the least-squares mm -> px affine --------------------------
corners = sorted(mm_by_corner)
mm_pts = np.array([[mm_by_corner[c]["x"], mm_by_corner[c]["y"], 1] for c in corners])
pxx = np.array([px_by_corner[c]["x"] for c in corners])
pxy = np.array([px_by_corner[c]["y"] for c in corners])
cx, _, _, _ = np.linalg.lstsq(mm_pts, pxx, rcond=None)
cy, _, _, _ = np.linalg.lstsq(mm_pts, pxy, rcond=None)
print("solved affine: px_x = %.5f*mm_x + %.5f*mm_y + %.3f" % tuple(cx))
print("               px_y = %.5f*mm_x + %.5f*mm_y + %.3f" % tuple(cy))
for c in corners:
    mx, my = mm_by_corner[c]["x"], mm_by_corner[c]["y"]
    resid = ((cx[0] * mx + cx[1] * my + cx[2] - px_by_corner[c]["x"]) ** 2
             + (cy[0] * mx + cy[1] * my + cy[2] - px_by_corner[c]["y"]) ** 2) ** 0.5
    assert resid < 5.0, f"affine residual too large at corner {c}: {resid:.2f}px"


def to_px(x: float, y: float) -> tuple[float, float]:
    return float(cx[0] * x + cx[1] * y + cx[2]), float(cy[0] * x + cy[1] * y + cy[2])


for side in ("J1", "J2"):
    for p in pads[side]:
        px, py = to_px(p["x"], p["y"])
        assert 0 <= px <= CW and 0 <= py <= CH, (
            f"{side} pad {p['pad']} (net {p['net']}) projects to ({px:.1f}, {py:.1f}), "
            f"outside the cropped {CW}x{CH} board"
        )

# --- label content/tag from net -----------------------------------------
gts = {s.gpio: s for s in mp.build_inventory() if s.gpio}


def label_for(net: str) -> tuple[str, str]:
    n = net.strip()
    if n == "2V5_3V3":
        return "2V5_3V3", "power"
    if n in ("+3V3", "3V3"):
        return "+3V3", "power"
    if "5V" in n:
        return "+5V", "power"
    if n == "GND":
        return "GND", "gnd"
    low = n.lower()  # GP7 -> gp7
    if low in gts:
        s = gts[low]
        return f"{low}  {s.connector}.{s.net}", mp.connector_key(s.connector)
    if low.startswith(("gp", "gn")):
        return f"{low} (spare)", "spare"
    return n, "spare"


# --- 5. layout ------------------------------------------------------------
LPAD = RPAD = 470
GAP = 46
BODY_W = 380
ROW = 30
TOP = 70
nmax = max(len(pads["J1"]), len(pads["J2"]))
band = max(CH, nmax * ROW)
CANVAS_W = LPAD + CW + RPAD
CANVAS_H = TOP + band + 40
board_top = TOP + (band - CH) / 2

diagram = Diagram(CANVAS_W, CANVAS_H, tag="board-pinout")

with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as f:
    crop.save(f, format="PNG")
    crop_path = f.name

label_count = 0


def add_side(side_pads: list[dict], left: bool) -> None:
    global label_count
    order = sorted(side_pads, key=lambda p: (round(to_px(p["x"], p["y"])[1]), to_px(p["x"], p["y"])[0]))
    n = len(order)
    col_top = TOP + (band - n * ROW) / 2
    for i, p in enumerate(order):
        content, tag = label_for(p["net"])
        px, py = to_px(p["x"], p["y"])
        ax, ay = LPAD + px, board_top + py       # pad anchor (diagram coords)
        ly = col_top + i * ROW + ROW / 2          # label row centre
        if left:
            dx, sc = ax - (LPAD - GAP), (-1, 1)   # fan left, body right-edge to board
        else:
            dx, sc = (LPAD + CW + GAP) - ax, (1, 1)
        diagram.add(PinLabelGroup(
            x=ax, y=ay, pin_pitch=(0, 0), label_start=(dx, ly - ay),
            label_pitch=(0, 0), scale=sc,
            labels=[[(content, tag, {"body": {"width": BODY_W, "height": ROW - 6}})]],
        ))
        label_count += 1


add_side(pads["J1"], left=True)
add_side(pads["J2"], left=False)

CSS_TEXT = """
svg { font-family: sans-serif; }
.pinlabel__text { text-anchor: middle; fill: #fff; font-size: 14px; font-weight:600; }
rect.pinlabel__body { rx:3; }
path.pinlabel__leader, path.pinlabel__leaderline { stroke:#555; stroke-width:1.6; fill:none; }
.spi0 rect.pinlabel__body { fill:#1b5e20; } .spi0 path.pinlabel__leaderline{stroke:#1b5e20;}
.spi1 rect.pinlabel__body { fill:#43a047; } .spi1 path.pinlabel__leaderline{stroke:#43a047;}
.uartbmc rect.pinlabel__body { fill:#0d47a1; } .uartbmc path.pinlabel__leaderline{stroke:#0d47a1;}
.com1 rect.pinlabel__body { fill:#1e88e5; } .com1 path.pinlabel__leaderline{stroke:#1e88e5;}
.com2 rect.pinlabel__body { fill:#3949ab; } .com2 path.pinlabel__leaderline{stroke:#3949ab;}
.jtag rect.pinlabel__body { fill:#b71c1c; } .jtag path.pinlabel__leaderline{stroke:#b71c1c;}
.gpio rect.pinlabel__body { fill:#4a148c; } .gpio path.pinlabel__leaderline{stroke:#4a148c;}
.power rect.pinlabel__body { fill:#e65100; } .power path.pinlabel__leaderline{stroke:#e65100;}
.gnd rect.pinlabel__body { fill:#111; } .gnd path.pinlabel__leaderline{stroke:#111;}
.spare rect.pinlabel__body { fill:#9e9e9e; } .spare path.pinlabel__leaderline{stroke:#9e9e9e;}
"""
diagram.add(Image(crop_path, x=LPAD, y=board_top, width=CW, height=CH, embed=True))
with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as f:
    f.write(CSS_TEXT)
    css_path = f.name
try:
    diagram.add_stylesheet(css_path, embed=True)
    svg = diagram.render()
finally:
    Path(css_path).unlink()
    Path(crop_path).unlink()

# splice a title + legend into the top band. LEG combines the split SPI/UART
# signal legend (mp.SIGNAL_LEGEND: spi0/spi1/uartbmc/com1/com2 + unchanged
# jtag/gpio) with this diagram's own power/GND/spare entries. Per-entry width
# scales with label length since "SPI1 BIOS-flash" etc. are wider than the
# old 4-word domain labels.
LEG = list(mp.SIGNAL_LEGEND) + [("power", "#e65100"), ("GND", "#111"), ("spare", "#9e9e9e")]
entry_w = [36 + 8 * len(name) for name, _ in LEG]
hdr = ['<rect x="0" y="0" width="%d" height="52" fill="#ffffff"/>' % CANVAS_W,
       '<text x="20" y="34" font-family="sans-serif" font-size="24" font-weight="bold" '
       'fill="#111">ULX3S J1/J2 &#8212; KGPE-D16 assignment, labels fan off each pad '
       '(board render: emard/ulx3s @6a92cec, MIT)</text>']
lx = CANVAS_W - sum(entry_w) - 20
for (name, col), w in zip(LEG, entry_w):
    hdr.append('<rect x="%d" y="18" width="16" height="16" fill="%s"/>' % (lx, col))
    hdr.append('<text x="%d" y="31" font-family="sans-serif" font-size="14" fill="#111">%s</text>'
               % (lx + 20, name))
    lx += w
svg = svg.replace("</svg>", "\n".join(hdr) + "\n</svg>")

OUT_SVG.write_text(svg)
print(f"wrote {OUT_SVG} ({CANVAS_W}x{CANVAS_H})")

# --- fail loud: validate the output --------------------------------------
assert "data:image/png;base64," in svg, "board render was not embedded as base64"
ET.parse(OUT_SVG)  # raises if not valid XML
expected = {f"{s.connector}.{s.net}" for s in mp.build_inventory()}
missing = [lbl for lbl in expected if lbl not in svg]
assert not missing, f"connector.net label(s) missing: {missing}"
print(f"pads: J1={len(pads['J1'])} J2={len(pads['J2'])} labels drawn: {label_count}")
print(f"connector.net assignments verified present: {len(expected)}")
