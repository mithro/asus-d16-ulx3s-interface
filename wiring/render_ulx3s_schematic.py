# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Regenerate wiring/ulx3s-gpio-schematic.svg from the upstream ULX3S KiCad
hardware design, for provenance/reproducibility.

This shallow-clones emard/ulx3s at the pinned commit, exports the schematic
hierarchy to SVG with `kicad-cli` (KiCad 9.0.2+, must be on PATH), and copies
the GPIO sheet's rendered SVG into wiring/. See wiring/ULX3S-SCHEMATIC-NOTICE.md
for the source/license details.

Run with: uv run wiring/render_ulx3s_schematic.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/emard/ulx3s"
PINNED_COMMIT = "6a92cec6b177191c5b0f80e260013a1f8ec147dd"
TOP_SCH = "ulx3s.sch"
GPIO_SHEET_SVG = "ulx3s-gpio.svg"

WIRING = Path(__file__).resolve().parent
REPO_ROOT = WIRING.parent
TMP_ROOT = REPO_ROOT / "tmp"
OUT_SVG = WIRING / "ulx3s-gpio-schematic.svg"


def run(cmd: list[str], **kwargs) -> None:
    """Run `cmd`, failing loud (no swallowed stderr, no silent non-zero exit)."""
    print(f"+ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"command failed (exit {result.returncode}): {' '.join(cmd)}")


def main() -> None:
    if shutil.which("kicad-cli") is None:
        raise RuntimeError("kicad-cli not found on PATH -- install KiCad (>=9.0) first")

    TMP_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP_ROOT) as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        clone_dir = tmp_dir / "ulx3s-src"
        svg_out_dir = tmp_dir / "sch-svg"
        svg_out_dir.mkdir()

        # (a) shallow-clone the pinned commit only.
        clone_dir.mkdir()
        run(["git", "init", "-q", str(clone_dir)])
        run(["git", "-C", str(clone_dir), "remote", "add", "origin", REPO_URL])
        run(["git", "-C", str(clone_dir), "fetch", "--depth", "1", "origin", PINNED_COMMIT])
        run(["git", "-C", str(clone_dir), "checkout", "-q", "FETCH_HEAD"])

        head = subprocess.run(
            ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if head.returncode != 0:
            raise RuntimeError("git rev-parse HEAD failed after checkout")
        got_commit = head.stdout.strip()
        if got_commit != PINNED_COMMIT:
            raise RuntimeError(
                f"checked out commit {got_commit!r} does not match pinned "
                f"commit {PINNED_COMMIT!r}"
            )

        # (b) export the schematic hierarchy to SVG (fail loud on non-zero exit).
        run([
            "kicad-cli", "sch", "export", "svg",
            str(clone_dir / TOP_SCH),
            "-o", str(svg_out_dir),
        ])

        produced = svg_out_dir / GPIO_SHEET_SVG
        if not produced.exists():
            raise RuntimeError(
                f"expected {produced} after kicad-cli export, but it was not produced "
                f"(found: {sorted(p.name for p in svg_out_dir.iterdir())})"
            )

        # (c) copy the GPIO sheet's SVG into wiring/ as the committed asset.
        shutil.copyfile(produced, OUT_SVG)
        print(f"wrote {OUT_SVG} (from {REPO_URL}@{PINNED_COMMIT[:12]})")


if __name__ == "__main__":
    main()
