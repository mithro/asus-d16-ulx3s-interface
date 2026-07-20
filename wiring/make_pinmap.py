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
Run with: uv run wiring/make_pinmap.py --svg       (also renders wiring/harness.svg)
Run with: uv run wiring/make_pinmap.py --headers   (also renders wiring/ulx3s-headers.svg
                                                     and wiring/rpi-header.svg, and writes
                                                     wiring/pi_pinmap.csv)
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
# ULX3S physical GPIO header layout (--headers)
# ---------------------------------------------------------------------------
#
# Source: emard/ulx3s doc/constraints/ulx3s_v20.lpf (FPGA ball assignments)
# and emard/ulx3s MANUAL.md (the physical J1/J2 header split). Each index i
# has a differential-capable gp[i]/gn[i] pair. J1 carries idx 0-13, J2
# carries idx 14-27. The LPF/MANUAL do not give through-hole pin *numbers*,
# only ball names -- so this table intentionally does not fabricate physical
# pin numbers; positions should be confirmed against the ULX3S board
# silkscreen when actually wiring.

@dataclass
class UlxHeaderPin:
    idx: int
    gp_ball: str
    gn_ball: str
    note: str = ""


ULX3S_HEADER_PINS: list[UlxHeaderPin] = [
    UlxHeaderPin(0, "B11", "C11", "PCLK"),
    UlxHeaderPin(1, "A10", "A11", "PCLK"),
    UlxHeaderPin(2, "A9", "B10", "GR_PCLK"),
    UlxHeaderPin(3, "B9", "C10", ""),
    UlxHeaderPin(4, "A7", "A8", ""),
    UlxHeaderPin(5, "C8", "B8", ""),
    UlxHeaderPin(6, "C6", "C7", ""),
    UlxHeaderPin(7, "A6", "B6", ""),
    UlxHeaderPin(8, "A4", "A5", "DIFF"),
    UlxHeaderPin(9, "A2", "B1", "DIFF"),
    UlxHeaderPin(10, "C4", "B4", "DIFF"),
    UlxHeaderPin(11, "F4", "E3", "DIFF, ESP32 wifi_gpio26"),
    UlxHeaderPin(12, "G3", "F3", "DIFF, ESP32 wifi_gpio33, PCLK"),
    UlxHeaderPin(13, "H4", "G5", "DIFF, ESP32 wifi_gpio35"),
    UlxHeaderPin(14, "U18", "U17", "DIFF, ADC AIN1/AIN0"),
    UlxHeaderPin(15, "N17", "P16", "DIFF, ADC AIN3/AIN2"),
    UlxHeaderPin(16, "N16", "M17", "DIFF, ADC AIN5/AIN4"),
    UlxHeaderPin(17, "L16", "L17", "DIFF, ADC AIN7/AIN6, GR_PCLK"),
    UlxHeaderPin(18, "H18", "H17", "DIFF"),
    UlxHeaderPin(19, "F17", "G18", "DIFF"),
    UlxHeaderPin(20, "D18", "E17", "DIFF"),
    UlxHeaderPin(21, "C18", "D17", "DIFF"),
    UlxHeaderPin(22, "B15", "C15", ""),
    UlxHeaderPin(23, "B17", "C17", ""),
    UlxHeaderPin(24, "C16", "D16", ""),
    UlxHeaderPin(25, "D14", "E14", ""),
    UlxHeaderPin(26, "B13", "C13", ""),
    UlxHeaderPin(27, "D13", "E13", ""),
]

assert len(ULX3S_HEADER_PINS) == 28, f"expected 28 ULX3S header indices, got {len(ULX3S_HEADER_PINS)}"

ULX3S_PIN_BY_IDX: dict[int, UlxHeaderPin] = {p.idx: p for p in ULX3S_HEADER_PINS}

# J1 = idx 0-13, J2 = idx 14-27 (emard/ulx3s MANUAL.md).
ULX3S_J1_IDX: range = range(0, 14)
ULX3S_J2_IDX: range = range(14, 28)

# gp/gn 11-13 are shared with the on-board ESP32 (see ESP32_RESERVED above).
ULX3S_ESP32_IDX: set[int] = {11, 12, 13}

# gp/gn 14-17 double as the onboard ADC channels (AIN0-7 across the four
# differential pairs). The harness assigns these anyway (see pinmap.csv) --
# a known, accepted tradeoff because this design never uses the ULX3S ADC.
ULX3S_ADC_SHARED_IDX: set[int] = {14, 15, 16, 17}

# Physical J1/J2 header row order, top -> bottom, as the pads actually sit on
# the board (from the ULX3S PCB, wiring/ulx3s-pads.json). Besides the gp/gn
# signal pairs, each 2x20 header carries power/GND pins: J1 has 2V5_3V3 supply
# + GND rails; J2 has +5V (via the STPS2L40AF diodes) and +3V3 + GND. J2 is
# ordered so idx 27 is at the top and idx 14 at the bottom, matching the board.
# Each row entry is ("sig", idx) or ("pwr", label, "power"|"gnd").
ULX3S_J1_ROWS: list[tuple] = [
    ("pwr", "2V5_3V3", "power"), ("pwr", "GND", "gnd"),
    ("sig", 0), ("sig", 1), ("sig", 2), ("sig", 3), ("sig", 4), ("sig", 5), ("sig", 6),
    ("pwr", "2V5_3V3", "power"), ("pwr", "GND", "gnd"),
    ("sig", 7), ("sig", 8), ("sig", 9), ("sig", 10), ("sig", 11), ("sig", 12), ("sig", 13),
    ("pwr", "GND", "gnd"), ("pwr", "2V5_3V3", "power"),
]
ULX3S_J2_ROWS: list[tuple] = [
    ("pwr", "+5V", "power"), ("pwr", "GND", "gnd"),
    ("sig", 27), ("sig", 26), ("sig", 25), ("sig", 24), ("sig", 23), ("sig", 22), ("sig", 21),
    ("pwr", "GND", "gnd"), ("pwr", "+3V3", "power"),
    ("sig", 20), ("sig", 19), ("sig", 18), ("sig", 17), ("sig", 16), ("sig", 15), ("sig", 14),
    ("pwr", "GND", "gnd"), ("pwr", "+3V3", "power"),
]


# ---------------------------------------------------------------------------
# Raspberry Pi 40-pin J8 header (--headers)
# ---------------------------------------------------------------------------
#
# Source: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
# (GPIO section). This physical pinout is identical from the Pi B+ through
# Pi 5 -- on the Pi 5, RP1 exposes BCM GPIO0-27 on these exact same physical
# pins, so this table applies unchanged there too.

@dataclass
class PiPin:
    phys: int
    label: str
    bcm: int | None  # None for power/ground rails (not a GPIO)


RPI_J8_HEADER: list[PiPin] = [
    PiPin(1, "3V3", None),
    PiPin(2, "5V", None),
    PiPin(3, "GPIO2 (SDA1)", 2),
    PiPin(4, "5V", None),
    PiPin(5, "GPIO3 (SCL1)", 3),
    PiPin(6, "GND", None),
    PiPin(7, "GPIO4 (GPCLK0)", 4),
    PiPin(8, "GPIO14 (TXD0)", 14),
    PiPin(9, "GND", None),
    PiPin(10, "GPIO15 (RXD0)", 15),
    PiPin(11, "GPIO17", 17),
    PiPin(12, "GPIO18 (PCM_CLK)", 18),
    PiPin(13, "GPIO27", 27),
    PiPin(14, "GND", None),
    PiPin(15, "GPIO22", 22),
    PiPin(16, "GPIO23", 23),
    PiPin(17, "3V3", None),
    PiPin(18, "GPIO24", 24),
    PiPin(19, "GPIO10 (SPI0 MOSI)", 10),
    PiPin(20, "GND", None),
    PiPin(21, "GPIO9 (SPI0 MISO)", 9),
    PiPin(22, "GPIO25", 25),
    PiPin(23, "GPIO11 (SPI0 SCLK)", 11),
    PiPin(24, "GPIO8 (SPI0 CE0)", 8),
    PiPin(25, "GND", None),
    PiPin(26, "GPIO7 (SPI0 CE1)", 7),
    PiPin(27, "GPIO0 (ID_SD)", 0),
    PiPin(28, "GPIO1 (ID_SC)", 1),
    PiPin(29, "GPIO5", 5),
    PiPin(30, "GND", None),
    PiPin(31, "GPIO6", 6),
    PiPin(32, "GPIO12 (PWM0)", 12),
    PiPin(33, "GPIO13 (PWM1)", 13),
    PiPin(34, "GND", None),
    PiPin(35, "GPIO19 (PCM_FS)", 19),
    PiPin(36, "GPIO16", 16),
    PiPin(37, "GPIO26", 26),
    PiPin(38, "GPIO20 (PCM_DIN)", 20),
    PiPin(39, "GND", None),
    PiPin(40, "GPIO21 (PCM_DOUT)", 21),
]

assert len(RPI_J8_HEADER) == 40, f"expected 40 J8 physical pins, got {len(RPI_J8_HEADER)}"

RPI_J8_BY_PHYS: dict[int, PiPin] = {p.phys: p for p in RPI_J8_HEADER}


# ---------------------------------------------------------------------------
# Pi5 -> DUT HIL wiring (--headers)
# ---------------------------------------------------------------------------
#
# The committed default HIL wiring from the Pi5's J8 header to the ULX3S/DUT.
# JTAG defaults verified against mithro/rp1-jtag's README (NeTV2 wiring);
# rp1-jtag pins are runtime-configurable, so these are defaults, not a fixed
# requirement.

@dataclass
class PiSignal:
    role: str
    pi_signal: str
    bcm: int
    phys_pin: int
    dir: str  # Pi-relative: "out" = Pi drives, "in" = Pi reads
    connects_to: str
    domain: str  # SPI, UART, JTAG, GPIO


def build_pi_pinmap() -> list[PiSignal]:
    """Pi 5 HIL harness: the Pi stands in for the DUT and exercises as much of
    the FPGA RTL as the 28 header GPIO allow, verifying it in priority order --
    (1) BOTH SPI-flash emulations, (2) the ASpeed JTAG, (3) the UARTs, (4) the
    remaining straps. The AMD HDT JTAG is intentionally left unverified (lowest
    priority; no spare pins). All 28 BCM GPIO (0-27) are used.

    Non-SPI lines are driven by the RP1 PIO (mithro/rp1-jtag,
    rpi5-rp1-pio-bench), so only SPI0/UART0 need their native alt-function pins;
    the rest are flexible. `dir` is Pi-relative and is the mirror of the
    FPGA-relative `dir` in pinmap.csv (an FPGA output is a Pi input, etc.).
    """
    sig = PiSignal
    return [
        # 1. SPI-flash emulation x2 -- Pi masters each bus and reads back the
        #    loaded image, verifying the FPGA's SPI-slave state machine.
        sig("SPI0 BMC-flash", "MOSI", 10, 19, "out", "BMC_FW1 flash DI (verify SPI-slave)", "SPI"),
        sig("SPI0 BMC-flash", "MISO", 9, 21, "in", "BMC_FW1 flash DO", "SPI"),
        sig("SPI0 BMC-flash", "SCLK", 11, 23, "out", "BMC_FW1 flash CLK", "SPI"),
        sig("SPI0 BMC-flash", "CE0", 8, 24, "out", "BMC_FW1 flash CS#", "SPI"),
        sig("SPI1 BIOS-flash", "MOSI", 20, 38, "out", "FU1 flash DI (verify SPI-slave)", "SPI"),
        sig("SPI1 BIOS-flash", "MISO", 19, 35, "in", "FU1 flash DO", "SPI"),
        sig("SPI1 BIOS-flash", "SCLK", 21, 40, "out", "FU1 flash CLK", "SPI"),
        sig("SPI1 BIOS-flash", "CE0", 18, 12, "out", "FU1 flash CS#", "SPI"),
        # 2. ASpeed JTAG -- Pi is the TAP target; the FPGA JTAG-master RTL scans
        #    it (FPGA drives TCK/TMS/TDI/TRST/SRST, reads TDO).
        sig("ASpeed-JTAG (Pi=TAP)", "TCK", 4, 7, "in", "AST_JTAG1 TCK (FPGA drives)", "JTAG"),
        sig("ASpeed-JTAG (Pi=TAP)", "TMS", 17, 11, "in", "AST_JTAG1 TMS", "JTAG"),
        sig("ASpeed-JTAG (Pi=TAP)", "TDI", 27, 13, "in", "AST_JTAG1 TDI (FPGA→TAP)", "JTAG"),
        sig("ASpeed-JTAG (Pi=TAP)", "TDO", 22, 15, "out", "AST_JTAG1 TDO (TAP→FPGA)", "JTAG"),
        sig("ASpeed-JTAG (Pi=TAP)", "NTRST", 23, 16, "in", "AST_JTAG1 NTRST", "JTAG"),
        sig("ASpeed-JTAG (Pi=TAP)", "SRST", 24, 18, "in", "AST_JTAG1 SRST#", "JTAG"),
        # 3. UARTs -- Pi is the DUT peer on each FPGA UART bridge (TTL side,
        #    before the COM1/COM2 MAX3232 level shifters).
        sig("UART0 BMC-console", "TXD", 14, 8, "out", "AST_UART1 → FPGA UART RX", "UART"),
        sig("UART0 BMC-console", "RXD", 15, 10, "in", "AST_UART1 ← FPGA UART TX", "UART"),
        sig("UART COM1", "TX", 12, 32, "out", "COM1 → FPGA RX (TTL, pre-MAX3232)", "UART"),
        sig("UART COM1", "RX", 13, 33, "in", "COM1 ← FPGA TX (TTL)", "UART"),
        sig("UART COM2", "TX", 16, 36, "out", "COM2 → FPGA RX (TTL)", "UART"),
        sig("UART COM2", "RX", 26, 37, "in", "COM2 ← FPGA TX (TTL)", "UART"),
        # 4. Straps -- read back FPGA-driven straps to verify the GPIO output
        #    RTL; drive one FPGA input (PLED) to verify the GPIO input RTL.
        sig("strap-verify", "IKVMEN#", 5, 29, "in", "BMC_FW1 IKVMEN# (FPGA drives)", "GPIO"),
        sig("strap-verify", "BMC_PRESENT#", 6, 31, "in", "BMC_FW1 BMC_PRESENT#", "GPIO"),
        sig("strap-verify", "SOLEN#", 7, 26, "in", "BMC_FW1 SOLEN#", "GPIO"),
        sig("strap-verify", "VGA_SW1", 25, 22, "in", "JUMPERS VGA_SW1", "GPIO"),
        sig("strap-verify", "IPMI_SEL", 2, 3, "in", "JUMPERS IPMI_SEL", "GPIO"),
        sig("strap-verify", "BIOS_RECOVERY#", 3, 5, "in", "JUMPERS BIOS_RECOVERY#", "GPIO"),
        sig("strap-verify", "CLRTC", 0, 27, "in", "JUMPERS CLRTC", "GPIO"),
        sig("strap-verify", "PLED", 1, 28, "out", "PANEL1 PLED (Pi drives, verify FPGA input RTL)", "GPIO"),
    ]


def validate_pi_pinmap(pi_signals: list[PiSignal]) -> None:
    """Cross-check every (bcm, phys_pin) against the canonical J8 table.

    This catches a wrong Pi pin assignment: if someone edits build_pi_pinmap()
    and gets the BCM number or physical pin wrong, this fails loud instead of
    silently wiring the wrong Pi pin.
    """
    seen_phys: dict[int, str] = {}
    for s in pi_signals:
        header_pin = RPI_J8_BY_PHYS.get(s.phys_pin)
        assert header_pin is not None, (
            f"{s.role}.{s.pi_signal}: phys pin {s.phys_pin} is not a valid J8 pin (1-40)"
        )
        assert header_pin.bcm == s.bcm, (
            f"{s.role}.{s.pi_signal}: claims BCM{s.bcm} at phys pin {s.phys_pin}, "
            f"but the J8 header has BCM{header_pin.bcm} at phys pin {s.phys_pin}"
        )
        assert header_pin.bcm is not None, (
            f"{s.role}.{s.pi_signal}: phys pin {s.phys_pin} is a power/ground rail, not a GPIO"
        )
        prev = seen_phys.get(s.phys_pin)
        assert prev is None, (
            f"phys pin {s.phys_pin} assigned to both {prev} and {s.role}.{s.pi_signal}"
        )
        seen_phys[s.phys_pin] = f"{s.role}.{s.pi_signal}"


# ---------------------------------------------------------------------------
# Signal inventory
# ---------------------------------------------------------------------------

def build_inventory() -> list[Signal]:
    """The signal inventory, every GPIO explicit.

    Allocation: ASpeed/BMC-side functions (AST_JTAG1, AST_UART1, BMC_FW1) sit
    on header J1 (idx 0-10; idx 11-13 are ESP32-reserved and left unused).
    Host/other-side functions (FU1, COM1/COM2, AMD_HDT, PANEL1, JUMPERS) sit
    on header J2 (idx 14-27). Within each header, each function's pins are
    contiguous, with an empty pin between function groups -- see
    wiring/README.md for the full rationale and the physical-pin-budget
    tradeoffs on J2.
    """
    sig = Signal
    return [
        # --- J1 (ASpeed/BMC side), idx 0-10 -----------------------------

        # AST_JTAG1 -- BMC ARM TAP, 3V3, via=direct. FPGA is JTAG master.
        # idx 0-3 (gn3 left as the gap before AST_UART1).
        sig("AST_JTAG1", "TCK", "out", "direct", gpio="gp0"),
        sig("AST_JTAG1", "TMS", "out", "direct", gpio="gp1"),
        sig("AST_JTAG1", "TDI", "out", "direct", gpio="gp2"),
        sig("AST_JTAG1", "TDO", "in", "direct", gpio="gp3"),
        sig("AST_JTAG1", "NTRST", "out", "direct", gpio="gn0"),
        sig("AST_JTAG1", "RTCK", "in", "direct", gpio="gn1"),
        sig("AST_JTAG1", "SRST#", "out", "direct", gpio="gn2"),
        # gn3, gp4, gn4 = gap between AST_JTAG1 and AST_UART1.

        # AST_UART1 -- BMC console, 3V3 TTL, via=direct. idx 5.
        sig("AST_UART1", "BMC_RXD", "out", "direct", gpio="gp5"),  # FPGA drives BMC's RX
        sig("AST_UART1", "BMC_TXD", "in", "direct", gpio="gn5"),  # FPGA reads BMC's TX
        # gp6, gn6 = gap between AST_UART1 and BMC_FW1.

        # BMC_FW1 -- BMC SPI flash R/W + straps, 3V3, via=direct. idx 7-10.
        # CS0/SCK/MOSI/MISO MUST stay spispy-cable-compatible (gp7-10 fixed).
        sig("BMC_FW1", "CS0", "in", "direct", gpio="gp7"),
        sig("BMC_FW1", "SCK", "in", "direct", gpio="gp8"),
        sig("BMC_FW1", "MOSI/SPIDO", "in", "direct", gpio="gp9"),
        sig("BMC_FW1", "MISO/SPIDI", "out", "direct", gpio="gp10"),
        sig("BMC_FW1", "CS2", "in", "direct", gpio="gn7"),
        sig("BMC_FW1", "IKVMEN#", "out", "direct", gpio="gn8"),  # strap
        sig("BMC_FW1", "BMC_PRESENT#", "out", "direct", gpio="gn9"),  # strap
        sig("BMC_FW1", "SOLEN#", "out", "direct", gpio="gn10"),  # strap
        # idx 11-13 = ESP32-reserved, left unused.

        # --- J2 (host/other side), idx 14-27 -----------------------------

        # FU1 -- host BIOS SPI flash, read-only, 3V3, via=direct. idx 14-16.
        sig("FU1", "CS#", "in", "direct", gpio="gp14"),
        sig("FU1", "CLK", "in", "direct", gpio="gp15"),
        sig("FU1", "MOSI/DI", "in", "direct", gpio="gp16"),
        sig("FU1", "MISO/DO", "out", "direct", gpio="gn14"),
        sig("FU1", "HOLD#", "in", "direct", gpio="gn15"),
        # gn16 = gap between FU1 and COM1/COM2.

        # COM1/COM2 -- host serial, via=MAX3232. idx 17-18.
        sig("COM1", "COM1_TX", "out", "MAX3232", gpio="gp17"),
        sig("COM1", "COM1_RX", "in", "MAX3232", gpio="gn17"),
        sig("COM2", "COM2_TX", "out", "MAX3232", gpio="gp18"),
        sig("COM2", "COM2_RX", "in", "MAX3232", gpio="gn18"),
        # idx 19 (gp19+gn19) fully empty = gap between serial and JTAG.

        # AMD_HDT -- host CPU HDT, via=1.27mm-adapter. FPGA is JTAG master.
        # idx 20-22.
        sig("AMD_HDT", "HDT_TCK", "out", "1.27mm-adapter", gpio="gp20"),
        sig("AMD_HDT", "HDT_TMS", "out", "1.27mm-adapter", gpio="gp21"),
        sig("AMD_HDT", "HDT_TDI", "out", "1.27mm-adapter", gpio="gp22"),
        sig("AMD_HDT", "HDT_TDO", "in", "1.27mm-adapter", gpio="gn20"),
        sig("AMD_HDT", "HDT_TRST_L", "out", "1.27mm-adapter", gpio="gn21"),
        # gn22 = gap between AMD_HDT and PANEL1/JUMPERS.

        # PANEL1 -- front panel, via=direct. idx 23-25.
        sig("PANEL1", "PWRBTN#", "out", "direct", gpio="gp23"),  # open-drain
        sig("PANEL1", "RESET#", "out", "direct", gpio="gp24"),  # open-drain
        sig("PANEL1", "NMIBNT#", "out", "direct", gpio="gp25"),  # open-drain
        sig("PANEL1", "PLED", "in", "direct", gpio="gn23"),
        sig("PANEL1", "HDLED", "in", "direct", gpio="gn24"),
        sig("PANEL1", "MLED", "in", "direct", gpio="gn25"),

        # JUMPERS -- drive to change state, via=direct. idx 26-27.
        sig("JUMPERS", "VGA_SW1", "out", "direct", gpio="gp26"),
        sig("JUMPERS", "IPMI_SEL", "out", "direct", gpio="gp27"),
        sig("JUMPERS", "BIOS_RECOVERY#", "out", "direct", gpio="gn26"),
        sig("JUMPERS", "CLRTC", "out", "direct", gpio="gn27"),
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

    # (f) header split: ASpeed/BMC-side connectors must land on J1
    # (idx 0-13), every other connector must land on J2 (idx 14-27).
    ASPEED_CONNECTORS: set[str] = {"BMC_FW1", "AST_UART1", "AST_JTAG1"}
    for s in signals:
        idx = int(s.gpio[2:])  # strip "gp"/"gn"
        on_j1 = idx in ULX3S_J1_IDX
        if s.connector in ASPEED_CONNECTORS:
            assert on_j1, (
                f"{s.connector}.{s.net} is an ASpeed/BMC signal but its GPIO "
                f"{s.gpio!r} (idx {idx}) is on J2, not J1 (idx 0-13)"
            )
        else:
            assert not on_j1, (
                f"{s.connector}.{s.net} is a host/other signal but its GPIO "
                f"{s.gpio!r} (idx {idx}) is on J1, not J2 (idx 14-27)"
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


def write_pi_csv(pi_signals: list[PiSignal], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["role", "pi_signal", "bcm", "phys_pin", "dir", "connects_to", "domain"])
        for s in pi_signals:
            writer.writerow([s.role, s.pi_signal, s.bcm, s.phys_pin, s.dir, s.connects_to, s.domain])


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

# Per-connector colour keys -- a finer split than DOMAIN_COLOR: the two SPI
# flashes (BMC boot flash vs host BIOS flash) and the three UART connectors
# (BMC console vs COM1 vs COM2) each get their own shade, while JTAG and GPIO
# stay single-colour. All keys take white text (see pinout-styles.css).
KEY_COLOR: dict[str, str] = {
    "spi0": "#1b5e20",  # dark green -- BMC_FW1 (BMC boot flash)
    "spi1": "#43a047",  # medium green -- FU1 (host BIOS flash)
    "uartbmc": "#0d47a1",  # dark blue -- AST_UART1 (BMC console)
    "com1": "#1e88e5",  # medium blue -- COM1
    "com2": "#3949ab",  # indigo blue -- COM2
    "jtag": "#b71c1c",  # dark red -- unchanged
    "gpio": "#4a148c",  # dark purple -- unchanged
}

# Which fine-grained colour key each connector belongs to.
CONNECTOR_KEY: dict[str, str] = {
    "BMC_FW1": "spi0",
    "FU1": "spi1",
    "AST_UART1": "uartbmc",
    "COM1": "com1",
    "COM2": "com2",
    "AST_JTAG1": "jtag",
    "AMD_HDT": "jtag",
    "PANEL1": "gpio",
    "JUMPERS": "gpio",
}


def connector_key(connector: str) -> str:
    """The fine-grained colour key (see KEY_COLOR) for a connector name."""
    return CONNECTOR_KEY[connector]


def connector_color(connector: str) -> str:
    """The fine-grained colour (see KEY_COLOR) for a connector name."""
    return KEY_COLOR[CONNECTOR_KEY[connector]]


def pi_role_key(role: str) -> str:
    """Map a build_pi_pinmap() `role` string to a KEY_COLOR key."""
    if role.startswith("SPI0"):
        return "spi0"
    if role.startswith("SPI1"):
        return "spi1"
    if role.startswith("UART0"):
        return "uartbmc"
    if role.startswith("UART COM1"):
        return "com1"
    if role.startswith("UART COM2"):
        return "com2"
    if role.startswith("ASpeed-JTAG"):
        return "jtag"
    return "gpio"  # strap-verify


# Canonical legend list for the split SPI/UART + unchanged JTAG/GPIO colour
# keys, in display order. Diagram-specific extra entries (power/GND/spare/
# ESP32/ADC, etc.) are appended after this in each renderer.
SIGNAL_LEGEND: list[tuple[str, str]] = [
    ("SPI0 BMC-flash", "#1b5e20"),
    ("SPI1 BIOS-flash", "#43a047"),
    ("UART BMC", "#0d47a1"),
    ("COM1", "#1e88e5"),
    ("COM2", "#3949ab"),
    ("JTAG", "#b71c1c"),
    ("GPIO", "#4a148c"),
]

BG_COLOR = "#ffffff"
TEXT_COLOR = "#111111"
LINE_COLOR = "#444444"

# Extra colours used only by the --headers diagrams (ulx3s-headers.svg,
# rpi-header.svg), for categories that aren't a Signal domain.
RESERVED_FILL = "#fbe9e7"  # light orange tint -- ESP32-reserved cell fill
RESERVED_STROKE = "#e65100"  # dark orange -- ESP32-reserved cell border/text
ADC_COLOR = "#e65100"  # dark orange -- ADC-shared corner marker (same hue as reserved)
UNASSIGNED_STROKE = "#9e9e9e"  # mid grey -- unassigned/plain cell border
POWER_COLOR = "#e65100"  # orange -- supply (2V5_3V3 / +5V / +3V3) rail cells
GND_COLOR = "#111111"  # black -- GND rail cells


def render_svg(signals: list[Signal], path: Path) -> None:
    """Render a three-column harness diagram derived from `signals`.

    Columns: KGPE-D16 connector pin (left) -- harness element / via (middle)
    -- ULX3S GPIO (right). Rows are grouped by connector, colour-coded by
    signal domain, and carry an arrowhead showing FPGA-relative direction.
    """
    MARGIN = 20
    ROW_H = 20
    ROW_SPACING = 24
    GROUP_GAP = 32  # blank band separating one connector group from the next
    GROUP_LABEL_H = 18

    LEFT_X = MARGIN
    LEFT_W = 230
    MID_X = LEFT_X + LEFT_W + 80
    MID_W = 150
    RIGHT_X = MID_X + MID_W + 80
    RIGHT_W = 70
    # The 7-entry SIGNAL_LEGEND (split SPI/UART) is wider than the 4-entry
    # domain legend used to be -- widen the canvas if the three-column layout
    # would leave it clipped.
    LEGEND_W = MARGIN + len(SIGNAL_LEGEND) * 130 + 40
    CANVAS_W = max(RIGHT_X + RIGHT_W + MARGIN, LEGEND_W)

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
    for label, color in SIGNAL_LEGEND:
        parts.append(f'<rect x="{legend_x}" y="{LEGEND_Y}" width="12" height="12" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x + 16}" y="{LEGEND_Y + 10}" fill="{TEXT_COLOR}">{escape(label)}</text>'
        )
        legend_x += 130

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
        color = connector_color(connector)
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


def render_ulx3s_headers(signals: list[Signal], path: Path) -> None:
    """Render wiring/ulx3s-headers.svg: the physical ULX3S J1/J2 GPIO headers
    in board row order, including the power/GND pins each header carries. Every
    gp/gn signal cell is joined against `signals` (the same in-memory list that
    produces pinmap.csv) to show what each pin is assigned to. J2 is oriented
    the way it sits on the board -- idx 27 at the top, idx 14 at the bottom.
    """
    gpio_to_signal: dict[str, Signal] = {s.gpio: s for s in signals if s.gpio is not None}

    # ESP32-reserved indices must never appear in the assigned map -- this is
    # already enforced by validate(), but re-check here since this diagram
    # asserts it visually too.
    for idx in ULX3S_ESP32_IDX:
        for which in ("gp", "gn"):
            name = f"{which}{idx}"
            assert name not in gpio_to_signal, f"{name} is ESP32-reserved but was assigned to a signal"

    MARGIN = 20
    CELL_W = 280
    CELL_H = 30
    ROW_SPACING = 36
    IDX_W = 46
    GAP = 30
    BLOCK_TITLE_H = 20
    BLOCK_GAP = 30
    TITLE_H = 30
    SUBTITLE_H = 28
    LEGEND_H = 20

    ROW_W = IDX_W + CELL_W + GAP + CELL_W
    # The 7-entry SIGNAL_LEGEND plus the ESP32-reserved/ADC-shared/unassigned/
    # supply/GND entries after it is wider than the 4-entry domain legend used
    # to be -- widen the canvas so the whole legend row fits and isn't clipped.
    LEGEND_TOTAL_W = MARGIN + len(SIGNAL_LEGEND) * 130 + 120 + 100 + 100 + 70 + 70
    CANVAS_W = max(ROW_W + 2 * MARGIN, LEGEND_TOTAL_W)

    top = MARGIN + TITLE_H + SUBTITLE_H + LEGEND_H + 20
    rows_per_block = 20  # full 2x20 physical header: gp/gn pairs + power/GND rows
    block_h = BLOCK_TITLE_H + rows_per_block * ROW_SPACING
    CANVAS_H = top + 2 * block_h + BLOCK_GAP + MARGIN

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}" '
        f'viewBox="0 0 {CANVAS_W} {CANVAS_H}" font-family="monospace" font-size="10">'
    )
    parts.append(f'<rect x="0" y="0" width="{CANVAS_W}" height="{CANVAS_H}" fill="{BG_COLOR}"/>')
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 16}" font-size="16" font-weight="bold" fill="{TEXT_COLOR}">'
        'ULX3S GPIO headers J1/J2 -- KGPE-D16 assignment</text>'
    )
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 30}" fill="{TEXT_COLOR}">'
        'Full 2x20 headers in board row order (from the ULX3S PCB): gp/gn pairs plus the '
        'power/GND pins. J1 = idx 0-13, J2 = idx 14-27, drawn 27 (top) -> 14 (bottom) as on the board. '
        'idx 11-13 shared with the on-board ESP32 (excluded from the usable pool).</text>'
    )
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 42}" fill="{TEXT_COLOR}">'
        'idx 14-17 double as onboard ADC AIN0-7 -- assigned anyway (ADC unused by this design). '
        'J1 supply = 2V5_3V3; J2 supply = +5V (via STPS2L40AF) and +3V3. FPGA ball in parens.</text>'
    )

    legend_y = MARGIN + TITLE_H + SUBTITLE_H
    legend_x = MARGIN
    for label, color in SIGNAL_LEGEND:
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" '
            f'fill="{color}" fill-opacity="0.2" stroke="{color}"/>'
        )
        parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">{escape(label)}</text>')
        legend_x += 130
    parts.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" '
        f'fill="{RESERVED_FILL}" stroke="{RESERVED_STROKE}" stroke-dasharray="2,2"/>'
    )
    parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">ESP32-reserved</text>')
    legend_x += 120
    parts.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" '
        f'fill="none" stroke="{ADC_COLOR}" stroke-width="2"/>'
    )
    parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">ADC-shared</text>')
    legend_x += 100
    parts.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" '
        f'fill="none" stroke="{UNASSIGNED_STROKE}" stroke-dasharray="2,2"/>'
    )
    parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">unassigned</text>')
    legend_x += 100
    parts.append(f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" fill="{POWER_COLOR}"/>')
    parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">supply</text>')
    legend_x += 70
    parts.append(f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" fill="{GND_COLOR}"/>')
    parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">GND</text>')

    cell_count = 0
    gp_gn_cells = 0
    y0 = top
    for block_name, rows in (("J1", ULX3S_J1_ROWS), ("J2", ULX3S_J2_ROWS)):
        parts.append(
            f'<text x="{MARGIN}" y="{y0 + BLOCK_TITLE_H - 6}" font-weight="bold" fill="{TEXT_COLOR}">'
            f'ULX3S {block_name}</text>'
        )
        y = y0 + BLOCK_TITLE_H
        for row in rows:
            if row[0] == "sig":
                idx = row[1]
                pin = ULX3S_PIN_BY_IDX[idx]
                parts.append(f'<text x="{MARGIN}" y="{y + CELL_H / 2 + 4}" fill="{TEXT_COLOR}">idx{idx}</text>')
                x = MARGIN + IDX_W
                for which, ball in (("gp", pin.gp_ball), ("gn", pin.gn_ball)):
                    gpio_name = f"{which}{idx}"
                    sig = gpio_to_signal.get(gpio_name)
                    line1 = f"{gpio_name} ({ball})"
                    if idx in ULX3S_ESP32_IDX:
                        fill_attr = f'fill="{RESERVED_FILL}"'
                        stroke = RESERVED_STROKE
                        dash_attr = 'stroke-dasharray="2,2"'
                        text_color = RESERVED_STROKE
                        line2 = "ESP32 reserved"
                    elif sig is not None:
                        color = connector_color(sig.connector)
                        fill_attr = f'fill="{color}" fill-opacity="0.15"'
                        stroke = color
                        dash_attr = ""
                        text_color = color
                        line2 = f"{sig.connector}.{sig.net}"
                    else:
                        fill_attr = 'fill="none"'
                        stroke = UNASSIGNED_STROKE
                        dash_attr = 'stroke-dasharray="2,2"'
                        text_color = UNASSIGNED_STROKE
                        line2 = pin.note if pin.note else "unassigned"
                    parts.append(
                        f'<rect x="{x}" y="{y}" width="{CELL_W}" height="{CELL_H}" {fill_attr} '
                        f'stroke="{stroke}" stroke-width="1.5" {dash_attr} rx="3"/>'
                    )
                    cell_count += 1
                    gp_gn_cells += 1
                    parts.append(
                        f'<text x="{x + 6}" y="{y + 12}" fill="{text_color}" font-weight="bold">'
                        f'{escape(line1)}</text>'
                    )
                    parts.append(f'<text x="{x + 6}" y="{y + 24}" fill="{text_color}">{escape(line2)}</text>')
                    if idx in ULX3S_ADC_SHARED_IDX:
                        parts.append(
                            f'<rect x="{x + CELL_W - 14}" y="{y + 2}" width="10" height="10" '
                            f'fill="none" stroke="{ADC_COLOR}" stroke-width="2"/>'
                        )
                    x += CELL_W + GAP
            else:
                _, net, kind = row
                color = POWER_COLOR if kind == "power" else GND_COLOR
                sub = "supply" if kind == "power" else "ground"
                parts.append(
                    f'<text x="{MARGIN}" y="{y + CELL_H / 2 + 4}" fill="{color}">'
                    f'{"PWR" if kind == "power" else "GND"}</text>'
                )
                x = MARGIN + IDX_W
                for _col in range(2):
                    parts.append(
                        f'<rect x="{x}" y="{y}" width="{CELL_W}" height="{CELL_H}" fill="{color}" '
                        f'stroke="{color}" stroke-width="1.5" rx="3"/>'
                    )
                    cell_count += 1
                    parts.append(
                        f'<text x="{x + 6}" y="{y + 12}" fill="#ffffff" font-weight="bold">{escape(net)}</text>'
                    )
                    parts.append(f'<text x="{x + 6}" y="{y + 24}" fill="#ffffff">{sub}</text>')
                    x += CELL_W + GAP
            y += ROW_SPACING
        y0 = y + BLOCK_GAP

    parts.append('</svg>')
    svg_text = "\n".join(parts)

    assert gp_gn_cells == 56, f"ULX3S gp/gn cell count {gp_gn_cells} != 56 (28 gp + 28 gn)"
    assert cell_count == 80, f"ULX3S header cell count {cell_count} != 80 (56 gp/gn + 24 power/GND)"
    print(f"ULX3S header cells drawn: {cell_count} (56 gp/gn + 24 power/GND)")

    path.write_text(svg_text)
    ET.parse(path)


def render_rpi_header(pi_signals: list[PiSignal], path: Path) -> None:
    """Render wiring/rpi-header.svg: the 40-pin Pi J8 header, in physical
    pin order, with the HIL role/connects_to from `pi_signals` (the same
    in-memory list that produces pi_pinmap.csv) overlaid on the pins in use.
    """
    phys_to_pi: dict[int, PiSignal] = {s.phys_pin: s for s in pi_signals}

    MARGIN = 20
    CELL_W = 340
    CELL_H = 28
    ROW_SPACING = 34
    COL_GAP = 40
    TITLE_H = 30
    SUBTITLE_H = 14
    LEGEND_H = 20
    TOP = MARGIN + TITLE_H + SUBTITLE_H + LEGEND_H + 20
    N_ROWS = 20

    # The 7-entry SIGNAL_LEGEND plus the "no HIL role" entry is wider than the
    # 4-entry domain legend used to be -- widen the canvas so it isn't clipped.
    LEGEND_TOTAL_W = MARGIN + len(SIGNAL_LEGEND) * 130 + 110
    CANVAS_W = max(MARGIN * 2 + CELL_W * 2 + COL_GAP, LEGEND_TOTAL_W)
    CANVAS_H = TOP + N_ROWS * ROW_SPACING + MARGIN

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}" '
        f'viewBox="0 0 {CANVAS_W} {CANVAS_H}" font-family="monospace" font-size="10">'
    )
    parts.append(f'<rect x="0" y="0" width="{CANVAS_W}" height="{CANVAS_H}" fill="{BG_COLOR}"/>')
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 16}" font-size="16" font-weight="bold" fill="{TEXT_COLOR}">'
        'Raspberry Pi J8 header -- HIL wiring to the ULX3S/DUT</text>'
    )
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 30}" fill="{TEXT_COLOR}">'
        'Canonical 40-pin J8 (raspberrypi.com GPIO docs), identical Pi B+..Pi 5 (RP1 keeps this '
        'layout). Highlighted pins are the committed pi_pinmap.csv defaults (rp1-jtag pins are '
        'runtime-configurable).</text>'
    )

    legend_y = MARGIN + TITLE_H + SUBTITLE_H
    legend_x = MARGIN
    for label, color in SIGNAL_LEGEND:
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" '
            f'fill="{color}" fill-opacity="0.2" stroke="{color}"/>'
        )
        parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">{escape(label)}</text>')
        legend_x += 130
    parts.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="12" height="12" '
        f'fill="none" stroke="{UNASSIGNED_STROKE}" stroke-dasharray="2,2"/>'
    )
    parts.append(f'<text x="{legend_x + 16}" y="{legend_y + 10}" fill="{TEXT_COLOR}">no HIL role</text>')

    cell_count = 0
    left_x = MARGIN
    right_x = MARGIN + CELL_W + COL_GAP
    for r in range(N_ROWS):
        phys_left = 2 * r + 1
        phys_right = 2 * r + 2
        y = TOP + r * ROW_SPACING

        for x, phys in ((left_x, phys_left), (right_x, phys_right)):
            pin = RPI_J8_BY_PHYS[phys]
            hil = phys_to_pi.get(phys)
            line1 = f"pin{phys:<2} {pin.label}"

            if hil is not None:
                color = KEY_COLOR[pi_role_key(hil.role)]
                fill_attr = f'fill="{color}" fill-opacity="0.15"'
                stroke = color
                dash_attr = ""
                text_color = color
                line2 = f"{hil.pi_signal} -> {hil.connects_to}"
            else:
                fill_attr = 'fill="none"'
                stroke = UNASSIGNED_STROKE
                dash_attr = 'stroke-dasharray="2,2"'
                text_color = TEXT_COLOR
                line2 = ""

            parts.append(
                f'<rect x="{x}" y="{y}" width="{CELL_W}" height="{CELL_H}" {fill_attr} '
                f'stroke="{stroke}" stroke-width="1.5" {dash_attr} rx="3"/>'
            )
            cell_count += 1
            parts.append(
                f'<text x="{x + 6}" y="{y + 12}" fill="{text_color}" font-weight="bold">{escape(line1)}</text>'
            )
            if line2:
                parts.append(
                    f'<text x="{x + 6}" y="{y + 24}" fill="{text_color}" font-size="9">{escape(line2)}</text>'
                )

    parts.append('</svg>')
    svg_text = "\n".join(parts)

    assert cell_count == 40, f"RPi header cell count {cell_count} != 40"
    print(f"RPi header cells drawn: {cell_count} (expected 40)")

    path.write_text(svg_text)
    ET.parse(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--svg",
        action="store_true",
        help="also render wiring/harness.svg from the same in-memory signal list",
    )
    parser.add_argument(
        "--headers",
        action="store_true",
        help=(
            "also render wiring/ulx3s-headers.svg and wiring/rpi-header.svg, and write "
            "wiring/pi_pinmap.csv"
        ),
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

    if args.headers:
        pi_signals = build_pi_pinmap()
        validate_pi_pinmap(pi_signals)

        pi_csv_path = Path(__file__).parent / "pi_pinmap.csv"
        write_pi_csv(pi_signals, pi_csv_path)
        print(f"wrote {pi_csv_path}")

        ulx3s_svg_path = Path(__file__).parent / "ulx3s-headers.svg"
        render_ulx3s_headers(signals, ulx3s_svg_path)
        print(f"wrote {ulx3s_svg_path}")

        rpi_svg_path = Path(__file__).parent / "rpi-header.svg"
        render_rpi_header(pi_signals, rpi_svg_path)
        print(f"wrote {rpi_svg_path}")


if __name__ == "__main__":
    main()
