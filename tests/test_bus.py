"""
Round-trip test scaffold for FlexibleSerial.

Structure ported from python-can's own serial interface test suite
(https://github.com/hardbyte/python-can/blob/main/test/serial_test.py):
a mocked-serial variant and a ``loop://`` variant of the same base test
case, so every test runs against both a fully in-memory fake serial port
and pyserial's real loopback URL handler.

The setUp/tearDown scaffolding is functional; the individual test methods
are stubs (ported names/docstrings only) left for follow-up work.
"""

import unittest
from unittest.mock import patch

from flexible_serial.bus import FlexibleSerial
from message_helper import ComparingMessagesTestCase

TIMEOUT = 0.1


class SerialDummy:
    """
    Minimal in-memory stand-in for a pyserial ``Serial`` instance, covering
    the subset of the API FlexibleSerial relies on (``write``,
    ``read_until``, ``timeout``, ``write_timeout``, ``port``, ``fileno``).
    """

    def __init__(self):
        self.port = "dummy"
        self.timeout = None
        self.write_timeout = None
        self._tx = bytearray()

    def write(self, data):
        self._tx += data

    def read_until(self, expected=b"\n", size=None):
        raise NotImplementedError

    def fileno(self):
        raise NotImplementedError

    def close(self):
        pass

    def reset(self):
        self._tx = bytearray()


class FlexibleSerialTestBase(ComparingMessagesTestCase):
    """
    Shared test bodies, run against both a mocked serial port
    (:class:`FlexibleSerialMockedTest`) and pyserial's ``loop://``
    (:class:`FlexibleSerialLoopTest`).
    """

    MAX_TIMESTAMP = 0xFFFFFFFF / 1000

    def __init__(self):
        ComparingMessagesTestCase.__init__(
            self, allowed_timestamp_delta=None, preserves_channel=True
        )

    def test_can_protocol(self):
        self.skipTest("not yet implemented")

    def test_rx_tx_min_max_data(self):
        """Tests the transfer from 0x00 to 0xFF for a 1 byte payload."""
        self.skipTest("not yet implemented")

    def test_rx_tx_min_max_dlc(self):
        """Tests the transfer from a 1 - 8 byte payload."""
        self.skipTest("not yet implemented")

    def test_rx_tx_data_none(self):
        """Tests the transfer without payload."""
        self.skipTest("not yet implemented")

    def test_rx_tx_min_std_id(self):
        """Tests the transfer with the lowest standard arbitration id."""
        self.skipTest("not yet implemented")

    def test_rx_tx_max_std_id(self):
        """Tests the transfer with the highest standard arbitration id."""
        self.skipTest("not yet implemented")

    def test_rx_tx_min_ext_id(self):
        """Tests the transfer with the lowest extended arbitration id."""
        self.skipTest("not yet implemented")

    def test_rx_tx_max_ext_id(self):
        """Tests the transfer with the highest extended arbitration id."""
        self.skipTest("not yet implemented")

    def test_rx_tx_max_timestamp(self):
        """Tests the transfer with the highest possible timestamp."""
        self.skipTest("not yet implemented")

    def test_rx_tx_max_timestamp_error(self):
        """Tests for an exception with an out of range timestamp (max + 1)."""
        self.skipTest("not yet implemented")

    def test_rx_tx_min_timestamp(self):
        """Tests the transfer with the lowest possible timestamp."""
        self.skipTest("not yet implemented")

    def test_rx_tx_min_timestamp_error(self):
        """Tests for an exception with an out of range timestamp (min - 1)."""
        self.skipTest("not yet implemented")

    def test_rx_tx_err_frame(self):
        """Test the transfer of error frames."""
        self.skipTest("not yet implemented")

    def test_rx_tx_rtr_frame(self):
        """Test the transfer of remote frames."""
        self.skipTest("not yet implemented")

    def test_when_no_fileno(self):
        """Tests for the fileno method catching a missing pyserial implementation."""
        self.skipTest("not yet implemented")


class FlexibleSerialMockedTest(unittest.TestCase, FlexibleSerialTestBase):
    def setUp(self):
        self.patcher = patch("serial.serial_for_url")
        self.mock_serial_for_url = self.patcher.start()
        self.serial_dummy = SerialDummy()
        self.mock_serial_for_url.return_value = self.serial_dummy
        self.addCleanup(self.patcher.stop)
        self.bus = FlexibleSerial("dummy", timeout=TIMEOUT)

    def tearDown(self):
        self.bus.shutdown()
        self.serial_dummy.reset()


class FlexibleSerialLoopTest(unittest.TestCase, FlexibleSerialTestBase):
    def setUp(self):
        self.bus = FlexibleSerial("loop://", timeout=TIMEOUT)

    def tearDown(self):
        self.bus.shutdown()
