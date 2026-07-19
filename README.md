# asus-d16-ulx3s-interface

A ULX3S (Lattice ECP5) LiteX SoC "bench controller" that terminates every
debug/control connector of an ASUS KGPE-D16 motherboard (ASPEED AST2050 BMC +
AMD host) through one fixed cable harness and exposes them to a Raspberry Pi 5
as standard USB devices (a wishbone debug bridge, CDC-ACM serial ports, XVC
JTAG probes, and serprog flash programmers via a soft USB hub) — for full
remote control/debug/development of both coreboot and BMC firmware. Successor
to [osresearch/spispy](https://github.com/osresearch/spispy).

## Architecture

The board presents itself to the Raspberry Pi 5 host over a single USB
connection as a soft USB hub exposing a set of standard USB device classes —
no custom host-side drivers required. Behind that hub sits a LiteX SoC built
for the ULX3S board (targeting the 45F FPGA during bring-up, with an eye
toward shrinking the design down to the 12F variant once cores stabilize).
The SoC fans out to the KGPE-D16's debug/control connectors: SPI flash
(serprog-compatible programmer, for coreboot/BMC flash emulation and
reprogramming), UART consoles (CDC-ACM serial ports), JTAG (XVC probes for
both the host and the AST2050 BMC), and a Wishbone debug bridge for direct
register-level access from the RPi 5. The Raspberry Pi 5 acts as the control
host, running the higher-level tooling and orchestrating hardware-in-the-loop
verification against the real motherboard.

**Status: early bring-up (P0/P1).**

## Quickstart

_To be filled in during P1 (LiteX SoC + platform definition)._

## Related

Part of the [mithro/ai-shenanigans-for-bmcs](https://github.com/mithro/ai-shenanigans-for-bmcs)
BMC firmware reverse-engineering and open-firmware project. This repo is the
hardware bench-controller companion used to drive and debug the ASUS
KGPE-D16 / AST2050 work done there.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
