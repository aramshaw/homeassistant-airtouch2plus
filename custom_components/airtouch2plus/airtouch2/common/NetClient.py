import asyncio
import errno
import logging
import socket
from typing import Callable
from airtouch2.common.interfaces import Callback, CoroCallback, Serializable, TaskCreator

_LOGGER = logging.getLogger(__name__)

# Cap connection attempts so a stalled connect (controller hung / not answering
# SYN) fails fast and we retry, rather than blocking on the OS timeout (~1-2 min).
CONNECT_TIMEOUT_SECONDS = 10

NetworkOrHostDownErrors = (errno.EHOSTUNREACH, errno.ECONNREFUSED,  errno.ETIMEDOUT,
                           errno.ENETDOWN, errno.ENETUNREACH, errno.ENETRESET, errno.ECONNABORTED)


def _format_exception(e: Exception):
    return f"{type(e).__name__}: {e}"


def _log_completed_task_exception(task: asyncio.Task) -> None:
    try:
        _ = task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        _LOGGER.error(f"{task.get_name()} completed with an exception:\n{_format_exception(e)}")


class NetClient:
    """A generic network client"""

    def __init__(self, host: str, port: int, on_connect: CoroCallback, handle_message: CoroCallback,
                 task_creator: TaskCreator = asyncio.create_task, on_disconnect: Callback | None = None):
        # network
        self._host_ip: str = host
        self._host_port: int = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        # async
        self._task_creator: Callable = task_creator
        self._main_loop_task: asyncio.Task[None] | None = None
        self._stop: bool = False

        self._on_connect = on_connect
        self._handle_message = handle_message
        self._on_disconnect = on_disconnect

    async def connect(self) -> bool:
        """Opens connection to the server, returns True/False if successful/unsuccessful"""
        _LOGGER.debug(f"Connecting to {self._host_ip} on port {self._host_port}")
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host_ip, self._host_port),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(f"Timed out connecting to {self._host_ip}:{self._host_port}")
            return False
        except OSError as e:
            _LOGGER.warning(f"Could not connect to host {self._host_ip}")
            if isinstance(e, socket.gaierror):
                # provided ip or port is rubbish/invalid
                pass
            elif e.errno not in NetworkOrHostDownErrors:
                raise e
            return False
        else:
            # No socket keepalive: the long-reliable original set no socket options.
            # An aggressive keepalive (5s idle / 10s user-timeout) was added from
            # upstream and is the prime suspect for coinciding with the controller's
            # network stack hanging overnight, so it is removed pending an A/B test.
            await self._on_connect()
            return True

    def run(self) -> None:
        """Starts the processing of incoming information from the server"""
        _LOGGER.debug("Starting listener task")
        self._stop = False
        self._main_loop_task = self._task_creator(self._main(), name="Main loop task")
        # This task typically has no awaiter but it's possible it completes
        # with an exception, so add a done callback that logs exceptions —
        # a silently-dead read loop is how the zombie-client bug hid.
        assert self._main_loop_task is not None
        self._main_loop_task.add_done_callback(_log_completed_task_exception)

    async def stop(self) -> None:
        """Stops the processing of incoming information from the server"""
        self._stop = True
        if self._main_loop_task is not None:
            self._main_loop_task.cancel()
            try:
                await self._main_loop_task
            except asyncio.CancelledError:
                # Eat the expected exception
                pass

    async def send(self, message: Serializable) -> None:
        """Send the serializable 'message'"""
        if self._writer is None:
            raise RuntimeError("Client is not connected - call connect() first")
        else:
            bytes_to_write = message.to_bytes()
            _LOGGER.debug(f"Sending {message.__class__.__name__} with data: {bytes_to_write.hex(':')}")
            _LOGGER.debug(f"{repr(message)}")
            self._writer.write(bytes_to_write)
            drained: bool = False
            while not drained:
                try:
                    await self._writer.drain()
                    drained = True
                except (asyncio.IncompleteReadError, OSError) as e:
                    # OSError covers reset / timeout / host-unreachable — any
                    # of them means the socket is dead; reconnect and retry.
                    await self._try_reconnect()

    async def read_bytes(self, size: int) -> bytes | None:
        """
        Read exactly 'size' bytes, return None if could not read enough bytes or on disconnection and reconnection.
        This coroutine handles reconnection.
        """
        if self._reader is None:
            raise RuntimeError("Client is not connected - call connect() first")
        try:
            data = await self._reader.readexactly(size)
        except asyncio.IncompleteReadError as e:
            _LOGGER.debug(f"IncompleteReadError - partial bytes: {e.partial.hex(':')}")
            data = None
        except OSError as e:
            # Any socket-level failure means the connection is gone: reset,
            # timeout, or host-unreachable (the controller dropping off WiFi
            # mid-connection raises [Errno 113], which is NOT a
            # ConnectionResetError). An uncaught error here kills the read
            # loop and leaves the client permanently dead until restart, so
            # treat every OSError as a lost connection and reconnect.
            _LOGGER.debug(f"Socket error while reading: {e!r}")
            data = None

        if data is None:
            _LOGGER.warning("Connection lost, reconnecting")
            await self._try_reconnect()
            return None
        _LOGGER.debug(f"Read payload of size {size}: {data.hex(':')}")
        return data

    async def _main(self) -> None:
        while not self._stop:
            if not (self._reader and self._writer):
                raise RuntimeError("Client is not connected - call connect() first")
            await self._handle_message()

    async def _try_reconnect(self) -> None:
        if self._on_disconnect is not None:
            self._on_disconnect()
        retries = 0
        while not await self.connect():
            await asyncio.sleep(0.001 * (10**retries) if retries < 4 else 10)
            retries += 1
            if not retries % 6 or retries == 4:
                _LOGGER.warning(
                    f"Controller {self._host_ip} not responding ({retries} attempts); still retrying")
        _LOGGER.info("Reconnected")