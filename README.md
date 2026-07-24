# flexible_serial

Template python-can interface plugin for a serial-backed CAN adapter.

This is intentionally a scaffold, not the finished Flexible Serial protocol
implementation. The Bus shape is based on python-can's existing `serial`
interface (`can.interfaces.serial.serial_can.SerialBus`) rather than the SLCAN
interface.

## Layout

```text
flexible_serial/
  pyproject.toml
  README.md
  src/
    flexible_serial/
      __init__.py
      bus.py
  tests/
    test_template_imports.py
  examples/
    send_once.py
```

## Install For Local Development

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\tools\python-can-flexible-serial[dev]
```

After installation, python-can can load the plugin through the `can.interface`
entry point:

```python
import can

bus = can.interface.Bus(
    interface="flexible_serial",
    channel="COM3",
    baudrate=115200,
)
```

The `sof` and `eof` frame delimiters can be passed as `bytes` from Python or
as hex strings from python-can CLI tools:

```powershell
uv run python -m can.logger -i flexible_serial -c COM3 --bus-kwargs sof=0xAA eof=0x0D0A
```

Prefer the `0x` prefix for CLI values, especially if the delimiter contains
only digits, because python-can auto-converts bare numeric kwargs.

## Usage Example

python -m can.viewer -i flexible_serial -c COM6 --bus-kwargs sof=0x4040 eof=0x0A uint16_id=True crc_type=crc16-ccitt flags_byte=False no_timestamp=True
python -m can.viewer -i flexible_serial -c /dev/ttyUSB0 --bus-kwargs sof=0x4040 eof=0x0A uint16_id=True crc_type=crc16-ccitt flags_byte=False no_timestamp=True



Bridging example
python -m can.bridge --bus1-interface flexible_serial --bus1-channel COM7 --bus1-bus-kwargs sof=0x4040 eof=0x0A uint16_id=True crc_type=crc16-ccitt flags_byte=False no_timestamp=True --bus2-interface kvaser --bus2-channel 0 --bus2-bitrate 125000


List Devices:
`python -c "import can; print(can.detect_available_configs(interfaces=['flexible_serial']))"`

binding usb device to wsl:
https://learn.microsoft.com/en-us/windows/wsl/connect-usb
in powershell:
`usbipd list`
`usbipd bind --busid 4-4`
`usbipd attach --wsl --busid <busid>`

in wsl
`python -m serial.tools.list_ports -v`

sudo modprobe usbserial
sudo modprobe ftdi_sio

`python`
`from flexible_serial import *`
`FlexibleSerial._detect_available_configs()`

Temporary solution
sudo chmod a+rw /dev/ttyUSB0

for cangaroo:
CANgaroo-x86_64.AppImage
mkdir -p ~/Applications/cangaroo/0.6.2
mv Cangaroo*.AppImage ~/Applications/cangaroo/0.6.2/CANgaroo-x86_64.AppImage
chmod +x ~/Applications/cangaroo/0.6.2/CANgaroo-x86_64.AppImage

Then make a stable command named cangaroo in .local/bin (symbolic link)
mkdir -p ~/.local/bin
ln -sf ~/Applications/cangaroo/0.6.2/CANgaroo-x86_64.AppImage ~/.local/bin/cangaroo

sym link:
ls -l ~/.local/bin
rm ~/.local/bin/cangaroo

might need
sudo apt install libopengl0