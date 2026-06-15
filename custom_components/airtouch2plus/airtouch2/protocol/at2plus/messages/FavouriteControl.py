from __future__ import annotations

from airtouch2.common.interfaces import Serializable
from airtouch2.protocol.at2plus.control_status_common import (
    CONTROL_STATUS_SUBHEADER_LENGTH,
    ControlStatusSubHeader,
    ControlStatusSubType,
    SubDataLength,
)
from airtouch2.protocol.at2plus.message_common import (
    AddressMsgType,
    Header,
    MessageType,
    add_checksum_message_buffer,
    prime_message_buffer,
)

# Second normal-data byte seen in both the 0x31 active selector and the working
# activation command. Its meaning is unknown but it is required for activation.
_ACTIVE_SELECTOR_FLAG = 0x08


class FavouriteControlMessage(Serializable):
    """Activate a favourite (scene) by id.

    Mirrors the 0x31 status's active selector - a bitmap of the target favourite
    in the normal data - but sent with control sub-type 0x30. The controller
    actuates the favourite's zones and broadcasts an updated 0x31 status.
    """

    def __init__(self, favourite_id: int):
        self.favourite_id = favourite_id

    def to_bytes(self) -> bytes:
        subheader = ControlStatusSubHeader(
            ControlStatusSubType.FAVOURITE_CONTROL, SubDataLength(2, 0, 0)
        )
        buffer = prime_message_buffer(
            Header(
                AddressMsgType.NORMAL,
                MessageType.CONTROL_STATUS,
                CONTROL_STATUS_SUBHEADER_LENGTH + subheader.subdata_length.total(),
            )
        )
        buffer.append(subheader)
        buffer.append_bytes(bytes([1 << self.favourite_id, _ACTIVE_SELECTOR_FLAG]))
        add_checksum_message_buffer(buffer)
        return buffer.to_bytes()
