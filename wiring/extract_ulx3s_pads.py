"""Extract J1/J2 pad positions + nets and mounting-hole positions from the
ULX3S PCB (emard/ulx3s @6a92cec, MIT).

This must be run with SYSTEM python3, NOT `uv run` -- it needs the `pcbnew`
module, which ships with a KiCad install (>=9.0) and is not uv-installable:

    python3 wiring/extract_ulx3s_pads.py <path/to/ulx3s.kicad_pcb> wiring/ulx3s-pads.json

Regenerates `wiring/ulx3s-pads.json`, the vendored geometry consumed by
`wiring/board_pinout.py`. To regenerate from a fresh clone of the pinned
upstream commit, see `wiring/render_ulx3s_board.py` (which clones the same
commit to produce the board render) -- clone that commit and pass its
`ulx3s.kicad_pcb` here.
"""
from __future__ import annotations

import json
import sys

import pcbnew


def mm(v: int) -> float:
    return round(pcbnew.ToMM(v), 4)


def extract(pcb_path: str) -> dict:
    board = pcbnew.LoadBoard(pcb_path)

    out: dict = {"J1": [], "J2": [], "holes": [], "edge_bbox": None}

    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref in ("J1", "J2"):
            for pad in fp.Pads():
                pos = pad.GetPosition()
                out[ref].append({
                    "pad": pad.GetNumber(),
                    "x": mm(pos.x), "y": mm(pos.y),
                    "net": pad.GetNetname(),
                })
        fpid = fp.GetFPIDAsString()
        if "MountingHole" in fpid:
            pos = fp.GetPosition()
            out["holes"].append({
                "ref": ref, "fpid": fpid, "x": mm(pos.x), "y": mm(pos.y),
            })

    bb = board.GetBoardEdgesBoundingBox()
    out["edge_bbox"] = {
        "x": mm(bb.GetX()), "y": mm(bb.GetY()),
        "w": mm(bb.GetWidth()), "h": mm(bb.GetHeight()),
    }

    # sort pads by number
    for k in ("J1", "J2"):
        out[k].sort(key=lambda p: int(p["pad"]))

    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: python3 extract_ulx3s_pads.py <ulx3s.kicad_pcb> <out.json>"
        )
    pcb_path, out_path = sys.argv[1], sys.argv[2]

    data = extract(pcb_path)

    assert len(data["J1"]) == 40, f"expected 40 J1 pads, got {len(data['J1'])}"
    assert len(data["J2"]) == 40, f"expected 40 J2 pads, got {len(data['J2'])}"
    assert len(data["holes"]) == 4, (
        f"expected exactly 4 MountingHole footprints, got {len(data['holes'])}: "
        f"{[h['ref'] for h in data['holes']]}"
    )

    with open(out_path, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")

    print(f"wrote {out_path}: J1={len(data['J1'])} pads, J2={len(data['J2'])} pads, "
          f"holes={[h['ref'] for h in data['holes']]}")


if __name__ == "__main__":
    main()
