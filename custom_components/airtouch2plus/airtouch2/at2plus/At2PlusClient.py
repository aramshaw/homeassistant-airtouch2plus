import asyncio
from datetime import datetime
import logging

from airtouch2.at2plus.At2PlusAircon import At2PlusAircon
from airtouch2.at2plus.At2PlusGroup import At2PlusGroup
from airtouch2.common.NetClient import NetClient
from airtouch2.protocol.at2plus.control_status_common import ControlStatusSubHeader, ControlStatusSubType, SubDataLength
from airtouch2.protocol.at2plus.extended_common import ExtendedMessageSubType, ExtendedSubHeader
from airtouch2.protocol.at2plus.message_common import HEADER_LENGTH, HEADER_MAGIC, Header, Message, MessageType
from airtouch2.protocol.at2plus.messages.AcAbilityMessage import AcAbility, AcAbilityMessage, RequestAcAbilityMessage
from airtouch2.protocol.at2plus.messages.AcStatus import AcStatusMessage
from airtouch2.common.Buffer import Buffer
from airtouch2.protocol.at2plus.crc16_modbus import crc16
from airtouch2.common.interfaces import Callback, Serializable, TaskCreator
from airtouch2.protocol.at2plus.messages.GroupNames import RequestGroupNamesMessage, group_names_from_subdata
from airtouch2.protocol.at2plus.messages.GroupStatus import GroupStatusMessage
from airtouch2.protocol.at2plus.messages.ExtendedStatus import ExtendedStatusMessage
from airtouch2.protocol.at2plus.messages.FavouriteStatus import (
    Favourite,
    FavouriteStatusMessage,
    RequestFavouriteStatusMessage,
)
from airtouch2.protocol.at2plus.messages.FavouriteControl import FavouriteControlMessage

_LOGGER = logging.getLogger(__name__)

# ACKs for the undocumented status broadcasts are sent with the control/status
# identifier (0xC0) in the address field. This is the value used by the proven,
# long-running implementation; whether the usual client address (0x80) would
# also be accepted for these messages has not been verified.
CONTROL_STATUS_REPLY_ADDRESS = 0xC0


class At2PlusClient:
    def __init__(self, host: str, dump_responses: bool = False, task_creator: TaskCreator = asyncio.create_task):
        # public
        self.aircons_by_id: dict[int, At2PlusAircon] = {}
        self.groups_by_id: dict[int, At2PlusGroup] = {}
        self.console_temperatures: dict[int, float] = {}
        self.favourites: list[Favourite] = []
        self.active_favourite_id: int | None = None
        self.connected: bool = False

        # private
        self._client = NetClient(
            host, 9200, self._on_connect, self.handle_one_message, task_creator,
            on_disconnect=self._on_disconnect,
        )
        self._dump_responses = dump_responses
        self._task_creator = task_creator
        self._new_ac_callbacks: list[Callback] = []
        self._ability_message_queue: asyncio.Queue[AcAbilityMessage] = asyncio.Queue()
        self._found_ac = asyncio.Event()
        self._new_group_callbacks: list[Callback] = []
        self._console_temperature_callbacks: list[Callback] = []
        self._favourite_callbacks: list[Callback] = []
        self._connection_callbacks: list[Callback] = []

        self.add_new_ac_callback(lambda: self._found_ac.set())

    async def connect(self) -> bool:
        return await self._client.connect()

    def run(self) -> None:
        self._client.run()

    async def wait_for_ac(self, timeout: int = 5) -> None:
        await asyncio.wait_for(self._found_ac.wait(), timeout)

    async def stop(self) -> None:
        await self._client.stop()

    def add_new_ac_callback(self, callback: Callback):
        self._new_ac_callbacks.append(callback)

        def remove_callback() -> None:
            if callback in self._new_ac_callbacks:
                self._new_ac_callbacks.remove(callback)

        return remove_callback

    def add_new_group_callback(self, callback: Callback):
        self._new_group_callbacks.append(callback)

        def remove_callback() -> None:
            if callback in self._new_group_callbacks:
                self._new_group_callbacks.remove(callback)

        return remove_callback

    def add_console_temperature_callback(self, callback: Callback):
        self._console_temperature_callbacks.append(callback)

        def remove_callback() -> None:
            if callback in self._console_temperature_callbacks:
                self._console_temperature_callbacks.remove(callback)

        return remove_callback

    def add_favourite_callback(self, callback: Callback):
        self._favourite_callbacks.append(callback)

        def remove_callback() -> None:
            if callback in self._favourite_callbacks:
                self._favourite_callbacks.remove(callback)

        return remove_callback

    def add_connection_callback(self, callback: Callback):
        """Register a callback fired whenever the connected state changes."""
        self._connection_callbacks.append(callback)

        def remove_callback() -> None:
            if callback in self._connection_callbacks:
                self._connection_callbacks.remove(callback)

        return remove_callback

    async def send(self, msg: Serializable):
        await self._client.send(msg)

    async def activate_favourite(self, favourite_id: int) -> None:
        """Activate a favourite (scene) by id. The controller actuates the
        favourite's zones and broadcasts an updated favourite status."""
        await self._client.send(FavouriteControlMessage(favourite_id))

    async def handle_one_message(self) -> None:
        message = await self._read_message()
        if not message:
            # something went wrong
            _LOGGER.warning("Reading message failed")
            return

        if message.header.type == MessageType.CONTROL_STATUS:
            subheader = ControlStatusSubHeader.from_buffer(message.data_buffer)
            if subheader.sub_type == ControlStatusSubType.AC_STATUS:
                status_message = AcStatusMessage.from_bytes(
                    message.data_buffer.read_bytes(subheader.subdata_length.total()))
                self._task_creator(self._handle_status_message(status_message))
            elif subheader.sub_type == ControlStatusSubType.GROUP_STATUS:
                group_status_message = GroupStatusMessage.from_bytes(
                    message.data_buffer.read_bytes(subheader.subdata_length.total()))
                self._task_creator(self._handle_group_status_message(group_status_message))
            elif subheader.sub_type == ControlStatusSubType.EXTENDED_STATUS:
                # Carries console temperatures and must also be acknowledged.
                subdata = message.data_buffer.read_bytes(subheader.subdata_length.total())
                self._update_console_temperatures(ExtendedStatusMessage.from_subdata(subheader, subdata))
                await self._acknowledge_status(subheader.sub_type)
            elif subheader.sub_type == ControlStatusSubType.IDENTITY:
                # Unsolicited broadcast that must be acknowledged to keep the connection alive.
                await self._acknowledge_status(subheader.sub_type)
            elif subheader.sub_type == ControlStatusSubType.FAVOURITE_STATUS:
                # Reply to our favourite request; carries the list + active favourite. No ACK.
                subdata = message.data_buffer.read_bytes(subheader.subdata_length.total())
                self._update_favourites(FavouriteStatusMessage.from_data(subheader, subdata))
            else:
                _LOGGER.warning(
                    f"Unknown status message type: subtype={subheader.sub_type}, data={message.data_buffer.to_bytes().hex(':')}")
        elif message.header.type == MessageType.EXTENDED:
            subheader = ExtendedSubHeader.from_buffer(message.data_buffer)
            if subheader.sub_type == ExtendedMessageSubType.ABILITY:
                ability_message_bytes = message.data_buffer.read_remaining()
                _LOGGER.debug(f"Creating ability message from {len(ability_message_bytes)} bytes")
                ability = AcAbilityMessage.from_bytes(ability_message_bytes)
                await self._ability_message_queue.put(ability)
            elif subheader.sub_type == ExtendedMessageSubType.GROUP_NAME:
                group_names_subdata = message.data_buffer.read_remaining()
                for id, name in group_names_from_subdata(group_names_subdata).items():
                    self.groups_by_id[id]._update_name(name)
            elif subheader.sub_type == ExtendedMessageSubType.ERROR:
                # NYI
                pass
            else:
                _LOGGER.warning(
                    f"Unknown extended message type: subtype={subheader.sub_type}, data={message.data_buffer.to_bytes().hex(':')}")
        elif message.header.type == MessageType.POWER_STATUS:
            # System power on/off broadcast (0x01 = on, 0x00 = off). No ACK required.
            _LOGGER.debug(f"Power status broadcast: {message.data_buffer.to_bytes().hex(':')}")
        else:
            _LOGGER.warning(
                f"Unknown message type, header={message.header.to_bytes().hex(':')}, data={message.data_buffer.to_bytes().hex(':')}")

    async def _acknowledge_status(self, sub_type: ControlStatusSubType) -> None:
        """Acknowledge an unsolicited status broadcast (EXTENDED_STATUS / IDENTITY).

        If these broadcasts aren't acknowledged the controller silently stops
        responding on the TCP session, which presents as a dropped connection.
        The ACK is a minimal control/status message that echoes the subtype.
        """
        subheader = ControlStatusSubHeader(
            sub_type, SubDataLength(normal=1, repeat_count=0, repeat_length=0)
        )
        ack = subheader.to_bytes() + bytes(1)
        header = Header(CONTROL_STATUS_REPLY_ADDRESS, MessageType.CONTROL_STATUS, len(ack))
        await self._client.send(Message(header, Buffer.from_bytes(ack)))

    def _update_console_temperatures(self, message: ExtendedStatusMessage) -> None:
        if message.console_temperatures != self.console_temperatures:
            self.console_temperatures = message.console_temperatures
            for callback in self._console_temperature_callbacks:
                callback()

    def _update_favourites(self, message: FavouriteStatusMessage) -> None:
        self.favourites = message.favourites
        self.active_favourite_id = message.active_favourite_id
        for callback in self._favourite_callbacks:
            callback()

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            for callback in self._connection_callbacks:
                callback()

    def _on_disconnect(self) -> None:
        # Called by NetClient when the connection is lost (before it reconnects).
        self._set_connected(False)

    async def _read_magic(self) -> bytes:
        """Search for the two header magic bytes"""
        while True:  # exit via return on successful read of header magic
            byte = await self._client.read_bytes(1)
            while (byte is None or byte[0] != HEADER_MAGIC):
                byte = await self._client.read_bytes(1)

            byte = await self._client.read_bytes(1)
            if (byte is not None and byte[0] == HEADER_MAGIC):
                return bytes([HEADER_MAGIC, HEADER_MAGIC])

    async def _read_header(self) -> tuple[Header, bytes]:
        """Try to read header until successful. Returns the raw bytes as well for validating checksum"""
        while True:  # exit via return on successful read of header
            header_bytes = bytearray()
            header_bytes += await self._read_magic()
            header_remainder = await self._client.read_bytes(HEADER_LENGTH-2)

            if not header_remainder:
                _LOGGER.debug("Failed reading header, trying again")
                continue

            header_bytes += header_remainder
            try:
                header = Header.from_bytes(header_bytes)
                return (header, header_bytes)
            except ValueError as e:
                _LOGGER.debug(f"ValueError: {e}\nFailed reading header, trying again")

    async def _read_message(self) -> Message | None:
        "Try to read an entire message. Return None if reading was interrupted by network failure."
        header, header_bytes = await self._read_header()
        buffer = Buffer(header.data_length)

        data_bytes = await self._client.read_bytes(header.data_length)
        if not data_bytes:
            # interrupted during data reading
            return None
        if not buffer.append_bytes(data_bytes):
            _LOGGER.warning(
                f"Received incorrect number of bytes, expected {header.data_length} but received {buffer._head}")

        checksum = await self._client.read_bytes(2)
        if not checksum:
            # interrupted during checksum reading
            return None
        calculated_checksum = crc16(header_bytes[2:] + buffer._data)
        if (checksum != calculated_checksum):
            _LOGGER.warning(
                f"Checksum mismatch, ignoring message: Got {checksum.hex(':')}, expected {calculated_checksum.hex(':')}")
            return None

        if self._dump_responses:
            # blocks but is only used for dev and debugging
            with open('message_' + datetime.now().strftime("%m-%d-%Y_%H-%M-%S") + '.dump', 'wb') as f:
                f.write(header.to_bytes() + buffer.to_bytes() + checksum)

        return Message(header, buffer)

    async def _on_connect(self) -> None:
        self._set_connected(True)
        # request groups
        await self._client.send(GroupStatusMessage([]))
        # request ACs
        await self._client.send(AcStatusMessage([]))
        # request favourites (the controller does not broadcast these unsolicited)
        await self._client.send(RequestFavouriteStatusMessage())

    async def _handle_status_message(self, message: AcStatusMessage):
        _LOGGER.debug("Handling AC status message")
        for status in message.statuses:
            if status.id not in self.aircons_by_id.keys():
                _LOGGER.debug(f"New AC ({status.id}) found")
                self.aircons_by_id[status.id] = At2PlusAircon(status, self)
                for callback in self._new_ac_callbacks:
                    callback()
                ability = await self._request_ac_ability(status.id)
                while not ability:
                    ability = await self._request_ac_ability(status.id)
                self.aircons_by_id[status.id]._set_ability(ability)
                _LOGGER.debug(f"Set ability of AC{status.id}")
            self.aircons_by_id[status.id]._update_status(status)
            _LOGGER.debug(f"Updated AC {status.id} with value {status}")
        _LOGGER.debug("Finished handling AC status message")

    async def _request_ac_ability(self, id: int) -> AcAbility | None:
        _LOGGER.debug(f"Requesting ability of AC{id}")
        await self._client.send(RequestAcAbilityMessage(id))
        _LOGGER.debug("Waiting for ability message response...")
        ac_ability = await self._ability_message_queue.get()
        _LOGGER.debug("Got ability message response")
        if len(ac_ability.abilities) != 1:
            _LOGGER.warning(f"Expected ability of single requested AC but got {len(ac_ability.abilities)}")
            return None
        if ac_ability.abilities[0].ac_id != id:
            _LOGGER.warning(f"Requested ability of AC{id} but got AC{ac_ability.abilities[0].ac_id}")
            return None
        _LOGGER.debug(f"Got ability of AC{id}: {ac_ability.abilities[0]}")
        return ac_ability.abilities[0]

    async def _handle_group_status_message(self, message: GroupStatusMessage):
        _LOGGER.debug("Handling group status message")
        request_names: bool = False
        if not len(self.groups_by_id):
            request_names = True
        for status in message.statuses:
            if status.id not in self.groups_by_id.keys():
                _LOGGER.debug(f"New group ({status.id}) found")
                self.groups_by_id[status.id] = At2PlusGroup(status, self)
                for callback in self._new_group_callbacks:
                    callback()
            self.groups_by_id[status.id]._update_status(status)
            _LOGGER.debug(f"Updated group {status.id} with value {status}")
        _LOGGER.debug("Finished handling group status message")
        if request_names:
            _LOGGER.debug("Requesting all group names")
            await self._client.send(RequestGroupNamesMessage())
