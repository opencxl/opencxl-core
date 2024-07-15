from asyncio import Event, StreamReader, StreamWriter, create_task, start_server
from asyncio.exceptions import CancelledError
from enum import Enum, auto
from typing import ByteString, Callable

from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger

class Irq(Enum):
    # Host-side file ready to be read by device using CXL.cache
    HOST_READY = 0x01

    # Device-side results ready to be read by host using CXL.mem
    ACCEL_READY = 0x02

    # Host finished writing file to device via CXL.mem
    HOST_SENT = 0x03

IRQ_WIDTH = 1 # in bytes

class IrqHandler(RunnableComponent):
    _msg_to_interrupt_event: dict[Irq, Event]
    _callbacks: list[Callable]

    def __init__(
        self,
        device_name,
        host="localhost",
        irq_enable_port=9000,
    ):
        self._label = f"{device_name}:IrqHandler"
        self._host = host
        self._port = irq_enable_port
        self._callbacks = []

    def register_interrupt_handler(self, irq_msg: Irq, irq_recv_cb: Callable):
        """
        Registers a callback on the arrival of a specific interrupt.
        Cannot be done while IrqHandler is running.
        """
        ev = Event()
        async def _callback():
            await ev.wait()
            irq_recv_cb()
        self._callbacks.append(_callback)
        self._msg_to_interrupt_event[irq_msg] = ev

    async def _irq_handler(self, reader: StreamReader, writer: StreamWriter):
        msg = reader.read(IRQ_WIDTH)
        if not msg:
            logger.debug(self._create_message("Irq enable connection broken"))
            return
        if msg not in self._msg_to_interrupt_event:
            raise RuntimeError(f"Invalid IRQ: {msg}")
        self._msg_to_interrupt_event[msg].set()

    async def _create_server(self):
        server = await start_server(self._irq_handler, self._host, self._port)
        return server

    async def _run(self):
        try:
            server = await self._create_server()
            self._server_task = create_task(server.serve_forever())
            await self._change_status_to_running()
            await self._server_task
        except CancelledError:
            logger.info(self._create_message("Irq enable listener stopped"))
