from __future__ import annotations
from dataclasses import dataclass

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

# Each favourite entry in a 0x31 reply is 11 bytes: id, 8-byte ASCII name, 2 unknown.
FAVOURITE_ENTRY_LENGTH = 11


@dataclass
class Favourite:
    """A favourite (scene): its id and name."""

    id: int
    name: str


@dataclass
class FavouriteStatusMessage:
    """The favourites reported by a 0x31 reply: the list and which one is active."""

    active_favourite_id: int | None
    favourites: list[Favourite]

    @staticmethod
    def from_data(subheader: ControlStatusSubHeader, data: bytes) -> FavouriteStatusMessage:
        # Normal data, when present, is a bitmap of the active favourite:
        # bit n set => favourite n is active.
        active_favourite_id: int | None = None
        if subheader.subdata_length.normal > 0 and data[0] > 0:
            active_favourite_id = data[0].bit_length() - 1

        favourites: list[Favourite] = []
        offset = subheader.subdata_length.normal
        length = subheader.subdata_length.repeat_length
        for i in range(subheader.subdata_length.repeat_count):
            block = data[offset + i * length: offset + (i + 1) * length]
            name = block[1:9].split(b"\x00")[0].decode("ascii", errors="ignore")
            favourites.append(Favourite(id=block[0], name=name))

        return FavouriteStatusMessage(active_favourite_id, favourites)


class RequestFavouriteStatusMessage(Serializable):
    """Request the favourite list and active state.

    The controller does not broadcast favourite status unsolicited; it must be
    requested, the same way group and AC status are (empty repeat data).
    """

    def to_bytes(self) -> bytes:
        subheader = ControlStatusSubHeader(
            ControlStatusSubType.FAVOURITE_STATUS,
            SubDataLength(0, 0, FAVOURITE_ENTRY_LENGTH),
        )
        buffer = prime_message_buffer(
            Header(
                AddressMsgType.NORMAL,
                MessageType.CONTROL_STATUS,
                CONTROL_STATUS_SUBHEADER_LENGTH + subheader.subdata_length.total(),
            )
        )
        buffer.append(subheader)
        add_checksum_message_buffer(buffer)
        return buffer.to_bytes()
