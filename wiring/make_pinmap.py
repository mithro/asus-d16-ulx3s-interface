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
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


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


def main() -> None:
    signals = build_inventory()
    assign_gpios(signals)
    validate(signals)
    print_summary(signals)

    out_path = Path(__file__).parent / "pinmap.csv"
    write_csv(signals, out_path)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
