# python-can-flexible-serial

A [python-can](https://python-can.readthedocs.io/) interface plugin that speaks a
configurable, framed binary protocol over a serial port. It follows the
[python-can plugin interface](https://python-can.readthedocs.io/en/stable/plugin-interface.html),
so once installed it is usable anywhere python-can accepts an `interface=`
name — `can.Bus`, `python -m can.viewer`, `python -m can.logger`,
`python -m can.bridge`, etc. — alongside the other community interface
plugins listed on that page (`python-can-canine`, `python-can-cvector`,
`python-can-remote`, `python-can-sontheim`, `python-can-cando`,
`python-can-candle`, ...).

Unlike the fixed-layout `slcan` and `serial` interfaces bundled with
python-can, the frame layout here is configurable via constructor kwargs
(SOF/EOF delimiters, ID width, optional CRC, optional flags byte, optional
timestamp) so it can be adapted to match firmware on the other end of the
serial link without changing code.

## Frame layout

| Section | Field | Size | Optional / Configurable |
|---|---|---|---|
| — | SOF | 1+ bytes | Configurable delimiter (default `0xAA`) |
| Header | timestamp | 0 or 4 bytes | Optional — omitted when `no_timestamp=True` |
| Header | dlc | 1 byte | Always present |
| Header | flags | 0 or 1 byte | Optional — only present when `flags_byte=True` |
| Header | id | 2 or 4 bytes | Sizeable — 2 bytes when `uint16_id=True`, else 4 |
| Payload | data | 0-64 bytes | Sizeable — length equals `dlc` |
| — | crc | 0 or N bytes | Optional — size/presence set by `crc_type` |
| — | EOF | 1+ bytes | Configurable delimiter (default `0xBB`) |

Details on each section:

- **SOF / EOF** — arbitrary-length byte sequences used purely as framing
  delimiters. They are never covered by the CRC.
- **timestamp** *(optional, fixed 4 bytes when present)* — `uint32`
  milliseconds. Omit entirely with `no_timestamp=True` to save 4 bytes per
  frame if the far end doesn't supply/need one; python-can still assigns a
  local receive timestamp in that case.
- **dlc** *(always present, fixed 1 byte)* — raw payload length in bytes;
  also used to size the `data` section on receive.
- **flags** *(optional, fixed 1 byte when present)* — see "Flags byte" below
  for the bit layout. When absent, the same metadata is instead packed into
  spare bits of the `id` field (see below).
- **id** *(always present, sizeable: 2 or 4 bytes)* — arbitration ID.
  `uint16_id=True` shrinks it to 2 bytes but rejects extended (29-bit) IDs;
  the default 4-byte field supports both standard and extended IDs.
- **data** *(sizeable, 0-64 bytes)* — the CAN payload; length is whatever
  `dlc` says, up to 64 bytes for CAN FD-sized frames or 8 bytes otherwise.
- **crc** *(optional, sizeable: 0 or N bytes)* — set by `crc_type`
  (`"none"` or `"crc16-ccitt"`); covers the header and payload, but not
  SOF/EOF.

Byte order for the header and CRC fields is little-endian by default, or
big-endian when `big_endian=True`.

### Flags byte

When `flags_byte=True`, extended/error/remote/FD metadata is carried in a
dedicated byte instead of being packed into spare bits of the arbitration ID:

| Bit  | Mask | Meaning |
|------|------|---------|
| 0    | `0x01` | Error frame |
| 1    | `0x02` | Remote frame (RTR) |
| 2    | `0x04` | Extended (29-bit) ID |
| 3    | `0x08` | CAN FD frame |
| 4    | `0x10` | CAN FD bit-rate switch |
| 5    | `0x20` | Frame originated as RX (vs. TX) |

When `flags_byte=False`, extended/error/remote status is instead OR'd into
spare high bits of the 32-bit arbitration ID field (standard `python-can`
convention: `CAN_EFF_FLAG`, `CAN_ERR_FLAG`, `CAN_RTR_FLAG`). This only applies
when `uint16_id=False`, since a 16-bit ID field has no spare bits for flags.

### Error frames

Protocol-level errors (frame desync, bad DLC, EOF mismatch, bad CRC) are
surfaced as `Message` objects with `is_error_frame=True` rather than raising
exceptions, matching how other python-can interfaces report bus errors. Use
`FlexibleSerial.decode_serial_can_error(msg)` to get a human-readable
description of an error message's contents.

## Installation

How you install this depends on whether you're *using* it as a dependency
of some other project, or working on this repo itself.

### Using this plugin from another project

If that project is managed with [uv](https://docs.astral.sh/uv/), add this
repo as an editable path dependency, pointing at wherever you've cloned it:

```powershell
uv add --editable plugins/python-can-flexible-serial
```

Run this from *that* project's directory, not from within this repo — `uv
add` adds a dependency to the project in your current directory, so running
it from inside `python-can-flexible-serial` itself is a self-dependency and
uv will refuse it.

With plain pip, from that project's environment:

```powershell
python -m pip install -e /path/to/python-can-flexible-serial
```

### Working on this repo itself

Use `uv sync` (a uv-managed project installs itself editable by default):

```powershell
uv sync
```

Or with plain pip, from this repo's root:

```powershell
python -m pip install -e .
```

Either way, this registers the plugin with python-can via the `can.interface`
entry point (`flexible_serial = "flexible_serial.bus:FlexibleSerial"`), so it
becomes available as `interface="flexible_serial"` anywhere python-can
resolves interfaces, without any further imports.

## Usage

```python
import can

bus = can.interface.Bus(
    interface="flexible_serial",
    channel="COM3",
    baudrate=115200,
)
```

### Constructor arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `channel` | `str` | *(required)* | Serial device, e.g. `"COM3"` or `"/dev/ttyUSB0"` |
| `baudrate` | `int` | `115200` | Serial baud rate |
| `timeout` | `float` | `0.1` | Serial read timeout, in seconds |
| `rtscts` | `bool` | `False` | Enable RTS/CTS hardware handshake |
| `sof` | `bytes` \| hex `str` | `0xAA` | Start-of-frame delimiter |
| `eof` | `bytes` \| hex `str` | `0xBB` | End-of-frame delimiter |
| `big_endian` | `bool` | `False` | Byte order for header/CRC fields |
| `uint16_id` | `bool` | `False` | Use a 2-byte arbitration ID instead of 4-byte; rejects extended IDs |
| `flags_byte` | `bool` | `False` | Carry frame metadata in a dedicated byte (see "Flags byte" above) |
| `no_timestamp` | `bool` | `False` | Omit the 4-byte timestamp header field |
| `crc_type` | `str` | `"none"` | One of `"none"`, `"crc16-ccitt"` |
| `rx_bytes_callback` | `Callable[[bytes, bool], None] \| None` | `None` | Called with `(raw_bytes, is_rx)` whenever a frame is sent or a chunk of buffer is consumed on receive; useful for logging/debugging the raw wire traffic |

`sof` and `eof` accept either raw `bytes` (when constructing a `Bus` from
Python) or a hex string (when passed as a CLI `--bus-kwargs` value, since
python-can CLI tools pass kwargs as strings):

```powershell
python -m can.logger -i flexible_serial -c COM3 --bus-kwargs sof=0xAA eof=0x0D0A
```

Prefer the `0x` prefix for CLI hex values, especially if the delimiter
contains only digits, since python-can auto-converts bare numeric kwargs.

### CLI examples

```powershell
python -m can.viewer -i flexible_serial -c COM6 --bus-kwargs sof=0x4040 eof=0x0A uint16_id=True crc_type=crc16-ccitt flags_byte=False no_timestamp=True
```

Bridging to another interface:

```powershell
python -m can.bridge --bus1-interface flexible_serial --bus1-channel COM7 --bus1-bus-kwargs sof=0x4040 eof=0x0A uint16_id=True crc_type=crc16-ccitt flags_byte=False no_timestamp=True --bus2-interface kvaser --bus2-channel 0 --bus2-bitrate 125000
```

### Autodetection

Like the built-in `serial` interface, available serial ports are surfaced
through python-can's autodetection API:

```python
import can
print(can.detect_available_configs(interfaces=["flexible_serial"]))
```

## Layout

```text
python-can-flexible-serial/
  pyproject.toml
  README.md
  src/
    flexible_serial/
      __init__.py
      bus.py
  tests/
    test_hex_bytes.py
    test_bus.py
    message_helper.py
```

## Development

Install with the `dev` extra to pull in test dependencies (`pytest`):

```powershell
uv sync --extra dev
```
or
```powershell
python -m pip install -e .[dev]
```

Run the test suite with:

```
uv run -m pytest
```
or
```powershell
python -m pytest
```

`tests/test_bus.py` scaffolds round-trip tests against two backends — a fully
in-memory mocked serial port and pyserial's `loop://` — mirroring the
structure of python-can's own `serial` interface test suite. The test
bodies are currently stubs (`skipTest`); fill them in as coverage is added.

## Future improvement ideas

- **`can_filters` support** — already works today for free: `BusABC.recv()`
  always calls `_matches_filters()` in software unless `_recv_internal`
  reports the message as pre-filtered, and this interface never does. What's
  still missing is a hardware-style optimization: implement the optional
  `_apply_filters()` hook so frames can be dropped *before* the CRC check
  runs, instead of always paying the full decode+CRC cost and filtering
  afterward.
- Support the CAN FD protocol for the `BusABC` parent (`can_protocol`
  currently reports `CAN_20` unconditionally).
- Add an option for arbitrary (non-CAN) data length up to 255 bytes, since
  `dlc` is only 1 byte — useful for passing arbitrary serial payloads through
  python-can tooling without being constrained to CAN/CAN FD payload sizes.
