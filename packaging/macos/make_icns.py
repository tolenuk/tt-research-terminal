"""Pack the Chart Loupe PNGs into ui/assets/tt-research.icns (run after the PNGs change).

ICNS is a header ('icns', total length) and typed chunks; since macOS 10.7 every chunk below
may hold a plain PNG, so no image library is needed:

    uv run python packaging/macos/make_icns.py
"""

from __future__ import annotations

import struct
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "src" / "obb_terminal" / "ui" / "assets"
# chunk type -> pixel size of the PNG it holds (the @2x types reuse the next size up)
CHUNKS = {
    "icp4": 16, "icp5": 32, "icp6": 64, "ic07": 128, "ic08": 256, "ic09": 512,
    "ic11": 32, "ic12": 64, "ic13": 256, "ic14": 512,
}


def build(assets: Path = ASSETS) -> bytes:
    body = b""
    for kind, size in CHUNKS.items():
        png = (assets / f"tt-research-{size}.png").read_bytes()
        body += kind.encode("ascii") + struct.pack(">I", len(png) + 8) + png
    return b"icns" + struct.pack(">I", len(body) + 8) + body


if __name__ == "__main__":
    out = ASSETS / "tt-research.icns"
    out.write_bytes(build())
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
