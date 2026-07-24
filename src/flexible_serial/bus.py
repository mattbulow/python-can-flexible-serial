"""
A python-can interface plugin for a configurable, framed binary protocol
over a serial port. For example use over serial ports like "/dev/ttyS1" or
"/dev/ttyUSB0" on Linux machines or "COM1" on Windows.

Unlike python-can's fixed-layout ``serial``/``slcan`` interfaces, the frame
layout (SOF/EOF delimiters, ID width, optional CRC, optional flags byte,
optional timestamp) is configurable via constructor kwargs so it can be
matched to firmware on the other end of the link. See the README for the
full frame layout and kwarg reference, and
https://python-can.readthedocs.io/en/stable/plugin-interface.html for the
python-can plugin interface this implements.
"""

import io
import logging
import struct
from collections.abc import Sequence
from typing import TypedDict, Callable, Any, Optional, cast
import binascii
import time
from enum import IntEnum

from can import (
    BusABC,
    CanInitializationError,
    CanInterfaceNotImplementedError,
    CanOperationError,
    CanProtocol,
    CanTimeoutError,
    Message,
)
from can.typechecking import AutoDetectedConfig

logger = logging.getLogger("can.flexible_serial")

try:
    import serial.tools.list_ports
except ImportError:
    logger.warning(
        "You won't be able to use the serial can backend without "
        "the `pyserial` package installed!"
    )
    serial = None

# move these into the class
CAN_ERR_FLAG = 0x20000000
CAN_RTR_FLAG = 0x40000000
CAN_EFF_FLAG = 0x80000000
CAN_ID_MASK_EXT = 0x1FFFFFFF
CAN_ID_MASK_STD = 0x7FF

FLAG_BYTE_ERROR     = 0x01
FLAG_BYTE_RTR       = 0x02
FLAG_BYTE_EXTENDED  = 0x04
FLAG_BYTE_FD        = 0x08
FLAG_BYTE_FD_BRS    = 0x10
FLAG_BYTE_IS_RX     = 0x20

MAX_DATA_LEN_CLASSIC = 8
MAX_DATA_LEN_FD = 64

'''
Frame Layout (see README.md for the full reference):
- SOF           -> 1 or more bytes
HEADER
- timestamp     -> options: 0 or 4 bytes, uint32 milliseconds
- dlc           -> 1 byte
- flags byte    -> options 0 or 1 byte
- id            -> options 2 or 4 bytes
DATA PAYLOAD    -> 0 - 64 bytes
- CRC           -> optional (0 or some num of bytes)
- EOF           -> 1 or more bytes
'''

class TimeoutTimer():
    def __init__(self, timeout: Optional[float]):
        if timeout is None:
            self._is_infinite = True
            self._timeout = 0.0
        else:
            self._is_infinite = False
            self._timeout = max(0.0, timeout)
        self._start_time = time.monotonic()

    def expired(self) -> bool:
        if self._is_infinite:
            return False

        return (time.monotonic() - self._start_time) >= self._timeout
    
    def remaining_time(self) -> float | None:
        if self._is_infinite:
            return None

        elapsed = time.monotonic() - self._start_time
        return max(0.0, self._timeout - elapsed)


class FlexibleSerial(BusABC):
    """
    Enable basic can communication over a serial device using a flexible binary protocol

    .. note:: See :meth:`~_recv_internal` for some special semantics.
    """

    class SerialError(IntEnum):
        NONE = 0
        DESYNC = 1
        BAD_DLC = 2
        BAD_EOF = 3
        BAD_CRC = 4

    ERROR_TEXT = {
        SerialError.DESYNC: "Start-of-Frame desync",
        SerialError.BAD_DLC: f"DataLength Invalid (>{MAX_DATA_LEN_FD} bytes)",
        SerialError.BAD_EOF: "End-of-Frame mismatch",
        SerialError.BAD_CRC: "CRC Error",
    }

    CRC_TYPES = frozenset({"none","crc16-ccitt"})

    class FrameKwargs(TypedDict):
        """Constructor kwargs controlling the wire frame layout.

        :ivar big_endian: Byte order for header/CRC fields.
        :ivar sof: Start-of-frame delimiter (bytes, or hex string from CLI kwargs).
        :ivar eof: End-of-frame delimiter (bytes, or hex string from CLI kwargs).
        :ivar uint16_id: Use a 2-byte arbitration ID instead of 4-byte; rejects extended IDs.
        :ivar crc_type: One of :attr:`CRC_TYPES` (``"none"``, ``"crc16-ccitt"``).
        :ivar flags_byte: Carry extended/error/remote/FD metadata in a dedicated header byte.
        :ivar no_timestamp: Omit the 4-byte (uint32 milliseconds) timestamp header field.
        :ivar rx_bytes_callback: ``callback(raw_bytes, is_rx)`` invoked for every
            frame written or chunk of buffer consumed while reading; useful for
            logging/debugging raw wire traffic.
        """
        big_endian: bool
        sof: bytes
        eof: bytes
        uint16_id: bool
        crc_type: str
        flags_byte: bool
        no_timestamp: bool
        rx_bytes_callback: Callable | None


    DEFAULT_FRAME_KWARGS: FrameKwargs = {
        "big_endian": False,
        "sof": b"\xAA",
        "eof": b"\xBB",
        "uint16_id": False,
        "crc_type": "none",
        "flags_byte": False,
        "no_timestamp": False,
        "rx_bytes_callback": None
    }

    DataProcessedCallback = Callable[[bytes, bool], None]
    ''' Provides arguments [data bytes, is_rx] '''

    def __init__(
        self,
        channel: str,
        baudrate: int = 115200,
        timeout: float = 0.1,
        rtscts: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        :param channel:
            The serial device to open. For example "/dev/ttyS1" or
            "/dev/ttyUSB0" on Linux or "COM1" on Windows systems.

        :param baudrate:
            Baud rate of the serial device in bit/s (default 115200).

            .. warning::
                Some serial port implementations don't care about the baudrate.

        :param timeout:
            Timeout for the serial device read calls in seconds (default 0.1).

        :param rtscts:
            Turn hardware handshake (RTS/CTS) on and off (default false)

        :param kwargs:
            Frame-layout options (see :class:`FrameKwargs` and
            :attr:`DEFAULT_FRAME_KWARGS` for defaults), plus any remaining
            kwargs passed to the base class constructor, see
            :meth:`can.BusABC.__init__`. All frame-layout kwargs accept
            hex strings (e.g. ``"0xAA"``) in addition to their native type,
            since python-can CLI tools pass ``--bus-kwargs`` as strings.

        :raises ~can.exceptions.CanInitializationError:
            If the given parameters are invalid.
        :raises TypeError: If channel is empty/None
        :raises ~can.exceptions.CanInterfaceNotImplementedError:
            If the serial module is not installed.
        """

        if not serial:
            raise CanInterfaceNotImplementedError("the serial module is not installed")

        if not channel:
            raise TypeError("Must specify a serial port.")

        self.channel = channel
        self.channel_info = f"Flexible serial interface: {channel}"
        self._can_protocol = CanProtocol.CAN_20

        self._rx_buffer = bytearray()

        self._sof = self._coerce_bytes_kwarg(
            "sof",
            kwargs.pop("sof", self.DEFAULT_FRAME_KWARGS["sof"]),
        )
        self._eof = self._coerce_bytes_kwarg(
            "eof",
            kwargs.pop("eof", self.DEFAULT_FRAME_KWARGS["eof"]),
        )

        big_endian = self._pop_bool_kwarg(kwargs, "big_endian")
        no_timestamp = self._pop_bool_kwarg(kwargs, "no_timestamp")
        uint16_id = self._pop_bool_kwarg(kwargs, "uint16_id")
        flags_byte = self._pop_bool_kwarg(kwargs, "flags_byte")

        self._configure_header(
            big_endian=big_endian,
            no_timestamp=no_timestamp,
            uint16_id=uint16_id,
            flags_byte=flags_byte,
        )
        self._configure_crc(big_endian=big_endian, kwargs=kwargs)
        self._configure_frame_offsets(no_timestamp=no_timestamp)

        # Register user callback for when serial bytes are received/transmitted
        self._pop_rx_bytes_callback(kwargs)

        try:
            self._ser = serial.serial_for_url(
                channel, baudrate=baudrate, timeout=timeout, rtscts=rtscts
            )
        except ValueError as error:
            raise CanInitializationError(
                "Could not create the serial device"
            ) from error
         
        logger.info(f"Opened Serial Port {channel} with frame settings:\r\n"
              f"\tEndianness: {"big" if big_endian else "little"}\r\n"
              f"\tSOF: {self._sof}\r\n"
              f"\tEOF: {self._eof}\r\n"
              f"\tTimestamp: {not no_timestamp}\r\n"
              f"\tFlags Byte: {flags_byte}\r\n"
              f"\tID Length: {2 if uint16_id else 4} bytes\r\n"
              f"\tCRC: {self._crc_type}",
        )

        super().__init__(channel, **kwargs)

    def shutdown(self) -> None:
        """
        Close the serial interface.
        """
        super().shutdown()
        self._ser.close()

    def send(self, msg: Message, timeout: Optional[float] = None) -> None:
        """
        Send a message over the serial device.

        :param msg:
            Message to send.

            .. note:: If the timestamp is a float value it will be converted
                      to an integer.

        :param timeout:
            This parameter will be ignored. The timeout value of the channel is
            used instead.

        """

        # Reject extended frames if uint16 ID is selected
        if self._id_is_uint16_t and msg.is_extended_id:
            raise ValueError("uint16_id=True does not support extended CAN IDs")

        # Mask ID, optionally placing error flags into ID field if its extended AND there is flags byte
        arbitration_id = msg.arbitration_id & (CAN_ID_MASK_EXT if msg.is_extended_id else CAN_ID_MASK_STD)
        if not self._has_flags_byte and not self._id_is_uint16_t:
            if msg.is_extended_id:
                arbitration_id |= CAN_EFF_FLAG
            if msg.is_error_frame:
                arbitration_id |= CAN_ERR_FLAG
            if msg.is_remote_frame:
                arbitration_id |= CAN_RTR_FLAG

        # Generate timestamp even when frame format options omits it
        timestamp_int = max(0, min(int(msg.timestamp * 1000), 0xFFFFFFFF))

        # Construct frame
        frame = bytearray(self._sof)
        frame += self._header_pack(timestamp_int, msg, arbitration_id)
        frame += msg.data
        frame += self._crc_calculate(memoryview(frame)[self._crc_calc_start_offset:])
        frame += self._eof

        try:
            if timeout != self._ser.write_timeout:
                self._ser.write_timeout = timeout
            self._ser.write(frame)
            if self._on_data_processed_callback:
                self._on_data_processed_callback(frame, False)
        except serial.PortNotOpenError as error:
            raise CanOperationError("writing to closed port") from error
        except serial.SerialTimeoutException as error:
            raise CanTimeoutError() from error
        
    @staticmethod
    def decode_serial_can_error(msg: Message) -> str:
        '''
        Data Byte 0 | Data Byte 1
          DESYNC    | num bytes dropped
          BAD_DLC   | Received DLC
          BAD_EOF   |   n/a
          BAD_CRC   |   n/a
        '''
        if not msg.is_error_frame:
            return ""
        if len(msg.data) == 0:
            return "Unknown, no data bytes to decode error type"
        if msg.data[0] == FlexibleSerial.SerialError.DESYNC:
            text = FlexibleSerial.ERROR_TEXT[FlexibleSerial.SerialError.DESYNC]
            if len(msg.data) >= 2:
                return text + f" -> {msg.data[1]} bytes dropped"
            else:
                return text
        elif msg.data[0] == FlexibleSerial.SerialError.BAD_DLC:
            text = FlexibleSerial.ERROR_TEXT[FlexibleSerial.SerialError.BAD_DLC]
            if len(msg.data) >= 2:
                return text + f" -> Rx data length = {msg.data[1]}"
            else:
                return text
        elif msg.data[0] == FlexibleSerial.SerialError.BAD_EOF:
            return FlexibleSerial.ERROR_TEXT[FlexibleSerial.SerialError.BAD_EOF]
        elif msg.data[0] == FlexibleSerial.SerialError.BAD_CRC:
            return FlexibleSerial.ERROR_TEXT[FlexibleSerial.SerialError.BAD_CRC]
        else:
            return f"Unknown data bytes error encoding: {msg.data.hex(' ')}"
        
    def _recv_internal(
        self, timeout: Optional[float]
    ) -> tuple[Optional[Message], bool]:
        """
        Read a message from the serial device.

        :param timeout: length to block until a message is received

        :returns:
            Received message and :obj:`False` (because no filtering as taken place).
        """

        timer = TimeoutTimer(timeout)
        try:
            while (True):
                msg = self._try_decode_message_from_buffer()
                if msg is not None:
                    return (msg, False)

                # The parser needs more bytes; allow one serial read before
                # checking the deadline again so recv(0) still polls hardware.
                # update timeout with remaining time
                self._ser.timeout = timer.remaining_time()
                # get bytes in hw serial rx buffer, up to an eof delimiter match
                new_rx_bytes = self._ser.read_until(self._eof)
                self._rx_buffer.extend(new_rx_bytes)

                msg = self._try_decode_message_from_buffer()
                if msg is not None:
                    return (msg, False)

                if timer.expired():
                    return (None, False)
            
        except serial.SerialException as error:
            raise CanOperationError("could not read from serial") from error

    def fileno(self) -> int:
        try:
            return cast("int", self._ser.fileno())
        except io.UnsupportedOperation:
            raise NotImplementedError(
                "fileno is not implemented using current CAN bus on this platform"
            ) from None
        except Exception as exception:
            raise CanOperationError("Cannot fetch fileno") from exception

    @staticmethod
    def _detect_available_configs() -> Sequence[AutoDetectedConfig]:
        configs: list[AutoDetectedConfig] = []
        if serial is None:
            return configs

        for port in serial.tools.list_ports.comports():
            configs.append({"interface": "flexible_serial", "channel": port.device})
        return configs

    @classmethod
    def _pop_bool_kwarg(cls, kwargs: dict[str, Any], name: str) -> bool:
        value = kwargs.pop(name, cls.DEFAULT_FRAME_KWARGS[name])
        if not isinstance(value, bool):
            raise TypeError(f"'{name}' must be bool, not {type(value).__name__}")
        return value
    
    def _pop_rx_bytes_callback(self, kwargs: dict[str, Any]):
        value = kwargs.pop("rx_bytes_callback", None)
        if value is None:
            self._on_data_processed_callback = None
            return
        if not callable(value):
            raise TypeError("'rx_bytes_callback' must be callable")
        self._on_data_processed_callback = value

    def _configure_header(
        self,
        *,
        big_endian: bool,
        no_timestamp: bool,
        uint16_id: bool,
        flags_byte: bool,
    ) -> None:
        self._has_timestamp = not no_timestamp
        self._id_is_uint16_t = uint16_id
        self._has_flags_byte = flags_byte

        header_format = self._build_header_format(
            big_endian=big_endian,
            no_timestamp=no_timestamp,
            uint16_id=uint16_id,
            flags_byte=flags_byte,
        )
        self._header = struct.Struct(header_format)
        self._header_pack = self._select_header_pack()

    @staticmethod
    def _build_header_format(
        *,
        big_endian: bool,
        no_timestamp: bool,
        uint16_id: bool,
        flags_byte: bool,
    ) -> str:
        header_format = ">" if big_endian else "<"      # Little/Big Endian
        header_format += "" if no_timestamp else "I"    # Timestamp -> 0 or 4 bytes
        header_format += "B"                            # DLC       -> 1 byte
        header_format += "B" if flags_byte else ""      # Flags     -> 0 or 1 byte
        header_format += "H" if uint16_id else "I"      # ID        -> 2 or 4 bytes
        return header_format

    def _select_header_pack(self) -> Callable[[int, Message, int], bytes]:
        if (not self._has_timestamp and not self._has_flags_byte):
            return self._pack_header_no_timestamp_no_flags
        elif (not self._has_timestamp and self._has_flags_byte):
            return self._pack_header_no_timestamp_yes_flags
        elif (self._has_timestamp and not self._has_flags_byte):
            return self._pack_header_yes_timestamp_no_flags
        elif (self._has_timestamp and self._has_flags_byte):
            return self._pack_header_yes_timestamp_yes_flags

        raise RuntimeError("Unable to select header pack function")

    def _configure_crc(self, *, big_endian: bool, kwargs: dict[str, Any]) -> None:
        crc_type = kwargs.pop("crc_type", self.DEFAULT_FRAME_KWARGS["crc_type"])
        if not isinstance(crc_type, str):
            raise TypeError(f"'crc_type' must be str, not {type(crc_type).__name__}")

        crc_type = crc_type.lower()
        crc_type = "none" if crc_type == "" else crc_type
        if crc_type not in self.CRC_TYPES:
            raise ValueError(
                f"'crc_type' {crc_type} not supported, must be one of {self.CRC_TYPES}"
            )
        
        self._crc_type = crc_type
        if crc_type == "crc16-ccitt":
            self._has_crc = True
            self._crc_calculate = self._crc16_calculate_ccitt
            self._crc_struct = struct.Struct((">" if big_endian else "<") + "H")
            self._crc_size = self._crc_struct.size
        else:
            self._has_crc = False
            self._crc_calculate = self._crc_calculate_none
            self._crc_size = 0

    def _configure_frame_offsets(self, *, no_timestamp: bool) -> None:
        self._sof_len = len(self._sof)
        self._eof_len = len(self._eof)
        self._header_offset = self._sof_len
        self._payload_offset = self._header_offset + self._header.size

        self._crc_calc_start_offset = self._sof_len
        self._crc_calc_start_offset += 0 if no_timestamp else 4
        self._crc_calc_start_offset += 1

        self._min_frame_length = self._payload_offset + self._crc_size + self._eof_len

    @staticmethod
    def _coerce_bytes_kwarg(name: str, value: Any) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            byte_value = bytes(value)
        elif isinstance(value, str):
            byte_value = FlexibleSerial._parse_hex_bytes(name, value)
        else:
            raise TypeError(
                f"'{name}' must be bytes or a hex string, "
                f"not {type(value).__name__}"
            )

        if not byte_value:
            raise ValueError(f"'{name}' must not be empty")
        return byte_value

    @staticmethod
    def _parse_hex_bytes(name: str, value: str) -> bytes:
        hex_value = value.strip()
        hex_value = hex_value.replace("\\x", "").replace("\\X", "")
        hex_value = hex_value.replace("0x", "").replace("0X", "")

        for separator in (" ", "\t", "\r", "\n", "_", "-", ":", ","):
            hex_value = hex_value.replace(separator, "")

        if len(hex_value) % 2:
            raise ValueError(
                f"'{name}' hex string must contain an even number of digits"
            )

        try:
            return bytes.fromhex(hex_value)
        except ValueError as error:
            raise ValueError(f"'{name}' must be bytes or a hex string") from error

    # A different pack_header method for each possible header structure
    # They all use the same arguments so the call to self._pack_header(...) in send() doesn't fail
    def _pack_header_no_timestamp_no_flags(self, timestamp: int, msg: Message, arbitration_id: int) -> bytes:
        return self._header.pack(msg.dlc, arbitration_id)
    def _pack_header_no_timestamp_yes_flags(self, timestamp: int, msg: Message, arbitration_id: int) -> bytes:
        return self._header.pack(msg.dlc, self._build_flags_byte(msg), arbitration_id)
    def _pack_header_yes_timestamp_no_flags(self, timestamp: int, msg: Message, arbitration_id: int) -> bytes:
        return self._header.pack(timestamp, msg.dlc, arbitration_id)
    def _pack_header_yes_timestamp_yes_flags(self, timestamp: int, msg: Message, arbitration_id: int) -> bytes:
        return self._header.pack(timestamp, msg.dlc, self._build_flags_byte(msg), arbitration_id)

    def _crc_calculate_none(self, data: memoryview) -> bytes:
        """ Return an empty bytes if CRC is not implemented """
        return b""
    
    def _crc16_calculate_ccitt(self, data: memoryview) -> bytes:
        """
        CRC16-CCITT with 0xFFFF start seed using polynomial 0x1021
        """
        crc = binascii.crc_hqx(data, 0xFFFF)
        return self._crc_struct.pack(crc)
    
    @staticmethod
    def _build_flags_byte(msg: Message) -> int:
        """ Build and return a packed byte filed with CAN message metadata"""
        flags = 0
        if msg.is_extended_id:
            flags = FLAG_BYTE_EXTENDED
        if msg.is_error_frame:
            flags |= FLAG_BYTE_ERROR
        if msg.is_remote_frame:
            flags |= FLAG_BYTE_RTR
        if msg.bitrate_switch:
            flags |= FLAG_BYTE_FD_BRS
        if msg.is_fd:
            flags |= FLAG_BYTE_FD
        if msg.is_rx:
            flags |= FLAG_BYTE_IS_RX
        return flags

    def _build_rx_error_message(
        self,
        error: "FlexibleSerial.SerialError",
        *,
        timestamp: Optional[float] = None,
        arbitration_id: int = 0,
        is_extended_id: bool = False,
        is_fd: bool = False,
        is_rx: bool = True,
        bitrate_switch: bool = False,
        detail: Optional[int] = None,
    ) -> Message:
        if detail is None:
            data = bytes([int(error)])
        else:
            data = bytes([int(error), max(0, min(detail, 0xFF))])

        return Message(
            timestamp=time.time() if timestamp is None else timestamp,
            arbitration_id=arbitration_id,
            is_extended_id=is_extended_id,
            is_remote_frame=False,
            is_error_frame=True,
            dlc=len(data),
            data=data,
            is_fd=is_fd,
            is_rx=is_rx,
            bitrate_switch=bitrate_switch,
            channel=self._ser.port
        )

    def _try_decode_message_from_buffer(self) -> Optional[Message]:
        """Parse one complete message from the buffered serial bytes, if available."""

        if not self._rx_buffer:
            return None

        # Search for SOF and preserve any partial SOF at the end of the buffer.
        sof_index = self._rx_buffer.find(self._sof)
        if sof_index < 0:
            keep_len = 0
            max_keep_len = min(len(self._rx_buffer), self._sof_len - 1)
            for candidate_len in range(max_keep_len, 0, -1):
                if self._sof.startswith(bytes(self._rx_buffer[-candidate_len:])):
                    keep_len = candidate_len
                    break

            dropped_count = len(self._rx_buffer) - keep_len
            if dropped_count <= 0:
                return None

            if self._on_data_processed_callback:
                self._on_data_processed_callback(self._rx_buffer[:dropped_count], True)
            del self._rx_buffer[:dropped_count]
            return self._build_rx_error_message(
                self.SerialError.DESYNC,
                detail=dropped_count,
            )

        # Drop any junk before the next complete SOF.
        if sof_index > 0:
            if self._on_data_processed_callback:
                self._on_data_processed_callback(self._rx_buffer[:sof_index], True)
            del self._rx_buffer[:sof_index]
            return self._build_rx_error_message(
                self.SerialError.DESYNC,
                detail=sof_index,
            )

        # Wait until the smallest valid frame can fit in the buffer.
        if len(self._rx_buffer) < self._min_frame_length:
            return None

        # Unpack fixed header fields.
        header = self._header.unpack_from(self._rx_buffer, self._header_offset)
        header_idx = 0
        if self._has_timestamp:
            timestamp = header[header_idx] / 1000.0
            header_idx += 1
        else:
            timestamp = time.time()

        dlc = header[header_idx]
        header_idx += 1

        if self._has_flags_byte:
            flags_byte = header[header_idx]
            header_idx += 1
        else:
            flags_byte = None

        arbitration_id = header[header_idx]

        # Parse message metadata from flags byte, ID flags, or format defaults.
        if self._has_flags_byte:
            is_extended_id  = bool(flags_byte & FLAG_BYTE_EXTENDED)
            is_error_frame  = bool(flags_byte & FLAG_BYTE_ERROR)
            is_remote_frame = bool(flags_byte & FLAG_BYTE_RTR)
            is_can_fd       = bool(flags_byte & FLAG_BYTE_FD)
            is_can_fd_brs   = bool(flags_byte & FLAG_BYTE_FD_BRS)
            is_rx           = bool(flags_byte & FLAG_BYTE_IS_RX)
        elif self._id_is_uint16_t:
            is_extended_id  = False
            is_error_frame  = False
            is_remote_frame = False
            is_can_fd       = dlc > 8
            is_can_fd_brs   = False
            is_rx           = True
        else:
            is_extended_id  = bool(arbitration_id & CAN_EFF_FLAG)
            is_error_frame  = bool(arbitration_id & CAN_ERR_FLAG)
            is_remote_frame = bool(arbitration_id & CAN_RTR_FLAG)
            is_can_fd       = False
            is_can_fd_brs   = False
            is_rx           = True

        # Mask protocol flag bits out of the CAN ID.
        if is_extended_id:
            arbitration_id = arbitration_id & CAN_ID_MASK_EXT
        else:
            arbitration_id = arbitration_id & CAN_ID_MASK_STD

        # Validate DLC before trusting it as the payload length.
        if dlc > (MAX_DATA_LEN_FD if is_can_fd else MAX_DATA_LEN_CLASSIC):
            if self._on_data_processed_callback:
                self._on_data_processed_callback(self._rx_buffer[0], True)
            del self._rx_buffer[0]
            return self._build_rx_error_message(
                self.SerialError.BAD_DLC,
                timestamp=timestamp,
                arbitration_id=arbitration_id,
                is_extended_id=is_extended_id,
                is_fd=is_can_fd,
                is_rx=is_rx,
                bitrate_switch=is_can_fd_brs,
                detail=dlc,
            )

        # Compute variable-length frame boundaries.
        crc_offset = self._payload_offset + dlc
        eof_offset = crc_offset + self._crc_size
        frame_end = eof_offset + self._eof_len

        # Wait until the complete frame is buffered.
        if len(self._rx_buffer) < frame_end:
            return None

        # Validate EOF delimiter.
        if self._rx_buffer[eof_offset:frame_end] != self._eof:
            if self._on_data_processed_callback:
                self._on_data_processed_callback(self._rx_buffer[0], True)
            del self._rx_buffer[0]
            return self._build_rx_error_message(
                self.SerialError.BAD_EOF,
                timestamp=timestamp,
                arbitration_id=arbitration_id,
                is_extended_id=is_extended_id,
                is_fd=is_can_fd,
                is_rx=is_rx,
                bitrate_switch=is_can_fd_brs,
            )

        # Validate CRC when enabled.
        if self._has_crc:
            expected_crc = self._crc_calculate(
                memoryview(self._rx_buffer)[self._crc_calc_start_offset:crc_offset]
            )
            received_crc = self._rx_buffer[crc_offset:eof_offset]
            if expected_crc != received_crc:
                if self._on_data_processed_callback:
                    self._on_data_processed_callback(self._rx_buffer[:frame_end], True)
                del self._rx_buffer[:frame_end]
                return self._build_rx_error_message(
                    self.SerialError.BAD_CRC,
                    timestamp=timestamp,
                    arbitration_id=arbitration_id,
                    is_extended_id=is_extended_id,
                    is_fd=is_can_fd,
                    is_rx=is_rx,
                    bitrate_switch=is_can_fd_brs,
                )

        # Build decoded CAN message and consume the frame bytes.
        payload = bytearray(self._rx_buffer[self._payload_offset:crc_offset])
        if self._on_data_processed_callback:
            self._on_data_processed_callback(self._rx_buffer[:frame_end], True)
        del self._rx_buffer[:frame_end]
        return Message(
            timestamp=timestamp,
            arbitration_id=arbitration_id,
            is_extended_id=is_extended_id,
            is_remote_frame=is_remote_frame,
            is_error_frame=is_error_frame,
            dlc=dlc,
            data=payload,
            is_fd=is_can_fd,
            is_rx=is_rx,
            bitrate_switch=is_can_fd_brs,
            channel=self._ser.port
        )
