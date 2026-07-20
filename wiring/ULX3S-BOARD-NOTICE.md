# Provenance: `ulx3s-board-render.png`

This file is a **3D top render of the ULX3S PCB**, produced directly from the
official ULX3S KiCad hardware design -- it is not hand-drawn or reconstructed.

- Source repository: <https://github.com/emard/ulx3s>
- Pinned commit: `6a92cec6b177191c5b0f80e260013a1f8ec147dd` (2025-04-27)
- Source file: `ulx3s.kicad_pcb`
- Rendered with: `kicad-cli pcb render --side top --background transparent
  --quality high --width 2600 --height 1500 --zoom 1.0` (KiCad 9.0.2).
- Regeneration: see `wiring/render_ulx3s_board.py` in this directory.

## License

The ULX3S hardware design (including this PCB and its render) is MIT-licensed:

> Copyright (c) 2016-2018 EMARD
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this hardware, software, and associated documentation files (the
> "Product"), to deal in the Product without restriction, including without
> limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of the Product, and to permit persons to whom
> the Product is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Product.
>
> The logotypes "EMARD", "RADIONA" and "FER" shall remain unmodified, visible,
> and in the original location, shape and size on the top PCB silkscreen
> layer.
>
> THE PRODUCT IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
> FROM, OUT OF OR IN CONNECTION WITH THE PRODUCT OR THE USE OR OTHER DEALINGS
> IN THE PRODUCT.

Full text: `LICENSE.md` in the emard/ulx3s repository, copyright RADIONA / EMARD.
