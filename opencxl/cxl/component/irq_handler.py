from asyncio import Event, StreamReader, StreamWriter, create_task, start_server
from asyncio.exceptions import CancelledError
from typing import ByteString, Callable

from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger

class IrqHandler(RunnableComponent):
    msg_to_interrupt: dict[ByteString, Callable]
    def __init__(
        self,
        device_name,
        host="localhost",
        irq_enable_port=9000,
    ):
        self._label = f"{device_name}:IrqHandler"
        self._host = host
        self._port = irq_enable_port

    def register_interrupt_handler(self, irq_msg: ByteString, irq_recv_cb: Callable):
        """
        Cannot be done while IrqHandler is running.
        """
        self.msg_to_interrupt[irq_msg] = irq_recv_cb

    async def _irq_handler(self, reader: StreamReader, writer: StreamWriter):
        msg = reader.read(
            len(max(self.msg_to_interrupt, key=lambda m: len(m)))
        )
        if not msg:
            logger.debug(self._create_message("Irq enable connection broken"))
            return
        if msg not in self.msg_to_interrupt:
            raise RuntimeError(f"Invalid IRQ: {msg}")
        self.msg_to_interrupt[msg]()

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
