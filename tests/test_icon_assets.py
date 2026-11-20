import struct

from obb_terminal.ui.icon import ASSETS

PNG = b"\x89PNG\r\n\x1a\n"


def test_icns_holds_every_retina_size_as_png():
    data = (ASSETS / "tt-research.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    types, pos = [], 8
    while pos < len(data):
        kind, size = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        assert data[pos + 8:pos + 16] == PNG
        types.append(kind.decode())
        pos += size
    assert pos == len(data)
    assert {"icp4", "icp5", "ic07", "ic08", "ic09", "ic11", "ic12", "ic13", "ic14"} <= set(types)
