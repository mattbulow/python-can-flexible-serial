import pytest

from flexible_serial.bus import FlexibleSerial


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (b"\xaa\xbb", b"\xaa\xbb"),
        (bytearray(b"\xaa\xbb"), b"\xaa\xbb"),
        ("AA", b"\xaa"),
        ("aa", b"\xaa"),
        ("AABB", b"\xaa\xbb"),
        ("0xAA", b"\xaa"),
        ("0x0D0A", b"\r\n"),
        ("0xAA0xBB", b"\xaa\xbb"),
        ("AA-BB", b"\xaa\xbb"),
        ("AA:BB", b"\xaa\xbb"),
        ("AA_BB", b"\xaa\xbb"),
        ("AA,BB", b"\xaa\xbb"),
        (r"\xAA\xBB", b"\xaa\xbb"),
    ],
)
def test_coerce_bytes_kwarg_accepts_cli_hex(value, expected):
    assert FlexibleSerial._coerce_bytes_kwarg("sof", value) == expected


@pytest.mark.parametrize("value", ["", "A", "not-hex"])
def test_coerce_bytes_kwarg_rejects_invalid_hex(value):
    with pytest.raises(ValueError):
        FlexibleSerial._coerce_bytes_kwarg("sof", value)


def test_coerce_bytes_kwarg_rejects_non_bytes_non_string():
    with pytest.raises(TypeError):
        FlexibleSerial._coerce_bytes_kwarg("sof", 0xAA)
