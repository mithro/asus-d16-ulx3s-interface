# /// script
# requires-python = ">=3.11"
# dependencies = ["pinout", "numpy", "scipy", "pillow"]
# ///
"""Board-image pinout: labels fanning off each physical J1/J2 pad on the
ULX3S 3D board render (emard/ulx3s @6a92cec, MIT).

Unlike `wiring/pinout_diagrams.py` (which draws a schematic-style header
block), this diagram places each label at the pad's *actual* pixel position
on the rendered PCB image. The render carries no embedded coordinate
metadata, so calibration is done at runtime: the four M3 mounting holes are
detected as gold blobs in `wiring/ulx3s-board-render.png`, matched by corner
to the four holes' known mm positions in `wiring/ulx3s-pads.json`, and used
to solve a least-squares mm -> pixel affine transform.

Run with: uv run wiring/board_pinout.py
Regenerate `wiring/ulx3s-pads.json` first with `extract_ulx3s_pads.py`
(system python3 + pcbnew) if the upstream board design changes.
"""
from __future__ import annotations

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
from pinout.components.pinlabel import PinLabel, Body  # noqa: E402
from pinout.components import leaderline as lline  # noqa: E402

PADS_JSON = WIRING / "ulx3s-pads.json"
BOARD_PNG = WIRING / "ulx3s-board-render.png"
OUT_SVG = WIRING / "ulx3s-board-pinout.svg"

pads = json.loads(PADS_JSON.read_text())

# --- 1. load the render, get its actual pixel size ---------------------
render_im = PILImage.open(BOARD_PNG).convert("RGBA")
RENDER_W, RENDER_H = render_im.size
render_arr = np.asarray(render_im)

# --- 2. detect the 4 mounting-hole gold blobs at runtime ----------------
# (ported from tmp/detect_holes.py: gold pad copper reads distinctly from
# the green soldermask/silkscreen/black connectors elsewhere on the board)


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
    fifth_area = comps[4]["area"] if len(comps) > 4 else 0.0
    min_top4 = min(c["area"] for c in top4)
    max_top4 = max(c["area"] for c in top4)
    # The 4 mounting holes are the 4 largest gold blobs on the board and are
    # all roughly the same size; fail loud if that's not a clean signal
    # (either a smaller 5th blob crowds the threshold, or the "top 4" vary
    # wildly in size, meaning we likely picked up noise, not real holes).
    assert fifth_area < 0.6 * min_top4, (
        f"no clear gap between the 4th and 5th largest gold blobs "
        f"(4th smallest={min_top4:.0f}, 5th={fifth_area:.0f}) -- mounting-hole "
        f"detection is ambiguous"
    )
    assert max_top4 < 2.0 * min_top4, (
        f"the 4 largest gold blobs vary too much in size "
        f"({min_top4:.0f}..{max_top4:.0f}) to confidently be the 4 mounting holes"
    )
    return top4


detected_holes = detect_mounting_holes(render_arr)
print(f"detected {len(detected_holes)} mounting holes (render {RENDER_W}x{RENDER_H}):")
for h in detected_holes:
    print(f"  area={h['area']:.0f}  x={h['x']:.1f}  y={h['y']:.1f}")

# --- 3. match detected px blobs to the json's mm hole entries, by corner -
json_holes = [h for h in pads["holes"]]
assert len(json_holes) == 4, f"expected exactly 4 holes in {PADS_JSON}, got {len(json_holes)}"


def corner_key(x: float, y: float, xs: list[float], ys: list[float]) -> str:
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return ("L" if x < cx else "R") + ("T" if y < cy else "B")


mm_xs = [h["x"] for h in json_holes]
mm_ys = [h["y"] for h in json_holes]
mm_by_corner = {corner_key(h["x"], h["y"], mm_xs, mm_ys): h for h in json_holes}

px_xs = [h["x"] for h in detected_holes]
px_ys = [h["y"] for h in detected_holes]
px_by_corner = {corner_key(h["x"], h["y"], px_xs, px_ys): h for h in detected_holes}

assert set(mm_by_corner) == set(px_by_corner) == {"LT", "RT", "LB", "RB"}, (
    f"could not uniquely match 4 holes to 4 corners: mm corners={sorted(mm_by_corner)}, "
    f"px corners={sorted(px_by_corner)}"
)

# --- 4. solve the least-squares mm -> px affine transform ---------------
corners = sorted(mm_by_corner)
mm_pts = np.array([[mm_by_corner[c]["x"], mm_by_corner[c]["y"], 1] for c in corners])
pxx = np.array([px_by_corner[c]["x"] for c in corners])
pxy = np.array([px_by_corner[c]["y"] for c in corners])
cx, _, _, _ = np.linalg.lstsq(mm_pts, pxx, rcond=None)
cy, _, _, _ = np.linalg.lstsq(mm_pts, pxy, rcond=None)
print("solved affine: px_x = %.5f*mm_x + %.5f*mm_y + %.3f" % tuple(cx))
print("               px_y = %.5f*mm_x + %.5f*mm_y + %.3f" % tuple(cy))

# Sanity-check the fit: each matched hole should land within a few px of its
# detected centroid, or the affine is not trustworthy.
for c in corners:
    mx, my = mm_by_corner[c]["x"], mm_by_corner[c]["y"]
    px_pred = cx[0] * mx + cx[1] * my + cx[2]
    py_pred = cy[0] * mx + cy[1] * my + cy[2]
    resid = ((px_pred - px_by_corner[c]["x"]) ** 2 + (py_pred - px_by_corner[c]["y"]) ** 2) ** 0.5
    assert resid < 5.0, f"affine residual too large at corner {c}: {resid:.2f}px"


def to_px(x: float, y: float) -> tuple[float, float]:
    return float(cx[0] * x + cx[1] * y + cx[2]), float(cy[0] * x + cy[1] * y + cy[2])


# --- 5. fail loud if any J1/J2 pad projects outside the render bounds ----
for side in ("J1", "J2"):
    for p in pads[side]:
        px, py = to_px(p["x"], p["y"])
        assert 0 <= px <= RENDER_W and 0 <= py <= RENDER_H, (
            f"{side} pad {p['pad']} (net {p['net']}) projects to ({px:.1f}, {py:.1f}), "
            f"outside the {RENDER_W}x{RENDER_H} render"
        )

# --- label content/tag from net -----------------------------------------
gts = {s.gpio: s for s in mp.build_inventory() if s.gpio}


def label_for(net: str) -> tuple[str, str]:
    n = net.strip()
    if n in ("2V5_3V3",):
        return "2V5_3V3", "power"
    if n in ("+3V3", "3V3"):
        return "+3V3", "power"
    if n in ("+5V", "5V") or "5V" in n:
        return "+5V", "power"
    if n == "GND":
        return "GND", "gnd"
    low = n.lower()  # GP7 -> gp7
    if low in gts:
        s = gts[low]
        dom = mp.DOMAIN_BY_CONNECTOR[s.connector].lower()
        return f"{low}  {s.connector}.{s.net}", dom
    if low.startswith(("gp", "gn")):
        return f"{low} (spare)", "spare"
    return n, "spare"


# --- layout (same constants as the approved tmp/build.py scratch) -------
LPAD, RPAD, TOP = 470, 470, 60
BODY_W, ROW = 400, 34
diagram = Diagram(LPAD + RENDER_W + RPAD, RENDER_H + 2 * TOP, tag="pinout-board")
diagram.add(Image(str(BOARD_PNG), x=LPAD, y=TOP, width=RENDER_W, height=RENDER_H, embed=True))

label_count = 0


def add_side(side_pads: list[dict], left: bool) -> None:
    global label_count
    # order by pad number, spread labels evenly over a tall column, curved fan
    n = len(side_pads)
    col_h = n * ROW
    y0 = TOP + RENDER_H / 2 - col_h / 2
    for i, p in enumerate(side_pads):
        content, tag = label_for(p["net"])
        px, py = to_px(p["x"], p["y"])
        ax, ay = LPAD + px, TOP + py          # pad anchor in diagram coords
        ly = y0 + i * ROW + ROW / 2            # label row centre
        if left:
            bx = (LPAD - 40 - BODY_W) - ax     # body left of board
        else:
            bx = (LPAD + RENDER_W + 40) - ax   # body right of board
        diagram.add(PinLabel(
            content=content, x=ax, y=ay, tag=tag,
            body=Body(bx, ly - ay, BODY_W, ROW - 6, corner_radius=3),
            leaderline=lline.Curved("hh"),
        ))
        label_count += 1


add_side(pads["J1"], left=True)
add_side(pads["J2"], left=False)

CSS_TEXT = """
svg { font-family: sans-serif; }
.pinlabel__text { text-anchor: middle; fill: #fff; font-size: 15px; font-weight:600; }
rect.pinlabel__body { rx:3; }
path.pinlabel__leader, .pinlabel__leaderline { stroke:#555; stroke-width:1.5; fill:none; }
.spi rect.pinlabel__body { fill:#1b5e20; } .spi path.pinlabel__leaderline{stroke:#1b5e20;}
.uart rect.pinlabel__body { fill:#0d47a1; } .uart path.pinlabel__leaderline{stroke:#0d47a1;}
.jtag rect.pinlabel__body { fill:#b71c1c; } .jtag path.pinlabel__leaderline{stroke:#b71c1c;}
.gpio rect.pinlabel__body { fill:#4a148c; } .gpio path.pinlabel__leaderline{stroke:#4a148c;}
.power rect.pinlabel__body { fill:#e65100; } .power path.pinlabel__leaderline{stroke:#e65100;}
.gnd rect.pinlabel__body { fill:#111; } .gnd path.pinlabel__leaderline{stroke:#111;}
.spare rect.pinlabel__body { fill:#9e9e9e; } .spare path.pinlabel__leaderline{stroke:#9e9e9e;}
"""
with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as f:
    f.write(CSS_TEXT)
    css_path = f.name
try:
    diagram.add_stylesheet(css_path, embed=True)
    svg = diagram.render()
finally:
    Path(css_path).unlink()

# splice a title + legend into the top band
LEG = [("SPI", "#1b5e20"), ("UART", "#0d47a1"), ("JTAG", "#b71c1c"),
       ("GPIO", "#4a148c"), ("power", "#e65100"), ("GND", "#111"),
       ("spare", "#9e9e9e")]
hdr = ['<rect x="0" y="0" width="%d" height="46" fill="#ffffff"/>' % (LPAD + RENDER_W + RPAD)]
hdr.append('<text x="20" y="30" font-family="sans-serif" font-size="22" '
           'font-weight="bold" fill="#111">ULX3S J1/J2 &#8212; KGPE-D16 assignment, '
           'labels fanning off each pad (board render: emard/ulx3s @6a92cec, MIT)</text>')
lx = LPAD + RENDER_W + RPAD - len(LEG) * 120 - 20
for name, col in LEG:
    hdr.append('<rect x="%d" y="16" width="14" height="14" fill="%s"/>' % (lx, col))
    hdr.append('<text x="%d" y="28" font-family="sans-serif" font-size="13" fill="#111">%s</text>'
               % (lx + 18, name))
    lx += 120
svg = svg.replace("</svg>", "\n".join(hdr) + "\n</svg>")

OUT_SVG.write_text(svg)
print(f"wrote {OUT_SVG}")

# --- fail loud: validate the output --------------------------------------
assert "data:image/png;base64," in svg, "render was not embedded as base64 in the SVG"

ET.parse(OUT_SVG)  # raises if not valid XML

all_signals = mp.build_inventory()
expected_labels = {f"{s.connector}.{s.net}" for s in all_signals}
missing = [lbl for lbl in expected_labels if lbl not in svg]
assert not missing, f"connector.net label(s) missing from the board pinout SVG: {missing}"

print(f"pads: J1={len(pads['J1'])} J2={len(pads['J2'])} total={len(pads['J1']) + len(pads['J2'])}")
print(f"labels drawn: {label_count}")
print(f"connector.net assignments verified present: {len(expected_labels)}")
