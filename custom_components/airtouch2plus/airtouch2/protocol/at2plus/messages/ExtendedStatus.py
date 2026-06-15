from __future__ import annotations
from dataclasses import dataclass

from airtouch2.protocol.at2plus.control_status_common import ControlStatusSubHeader

# The 0x2B extended-status message carries several 4-byte sensor blocks. Blocks
# with these ids are the wall-console (touchscreen) temperature sensors. (Earlier
# protocol notes labelled them "system counters"; live capture showed the value
# tracking room temperature, matching a community finding that they are the
# console temperatures.)
CONSOLE_SENSOR_IDS = (0x90, 0x91)
_NO_READING = 0x07FF
# temperature_celsius = (raw_16bit - 512) / 10
_TEMP_OFFSET = 512


@dataclass
class ExtendedStatusMessage:
    """The console temperatures carried by a 0x2B extended-status broadcast.

    Only the console temperature blocks are decoded here. The favourite and
    timer/counter fields the 0x2B message also carries are not yet interpreted.
    """

    console_temperatures: dict[int, float]  # 0-based console index -> degrees C

    @staticmethod
    def from_subdata(
        subheader: ControlStatusSubHeader, subdata: bytes
    ) -> ExtendedStatusMessage:
        """Parse console temperatures from the repeating sensor blocks."""
        temperatures: dict[int, float] = {}
        block_length = subheader.subdata_length.repeat_length
        start = subheader.subdata_length.normal
        for i in range(subheader.subdata_length.repeat_count):
            block = subdata[start + i * block_length : start + (i + 1) * block_length]
            if len(block) < 4:
                continue
            sensor_id = block[0]
            if sensor_id in CONSOLE_SENSOR_IDS:
                raw = int.from_bytes(block[2:4], "big")
                if raw != _NO_READING:
                    index = sensor_id - CONSOLE_SENSOR_IDS[0]
                    temperatures[index] = round((raw - _TEMP_OFFSET) / 10, 1)
        return ExtendedStatusMessage(console_temperatures=temperatures)
