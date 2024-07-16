"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import (
    Event,
    StreamReader,
    StreamWriter,
    Task,
    create_task,
    gather,
    start_server,
    open_connection,
)
from asyncio.exceptions import CancelledError
from enum import Enum
from typing import Callable, Optional

from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger


class Irq(Enum):
    # Host-side file ready to be read by device using CXL.cache
    HOST_READY = 0x01

    # Device-side results ready to be read by host using CXL.mem
    ACCEL_VALIDATION_FINISHED = 0x02

    # Host finished writing file to device via CXL.mem
    HOST_SENT = 0x03

    # Accelerator finished training, waiting for host to send validation pics
    ACCEL_TRAINING_FINISHED = 0x04


IRQ_WIDTH = 1  # in bytes


class IrqManager(RunnableComponent):
    _msg_to_interrupt_event: dict[Irq, Event]
    _callbacks: list[Callable]
    _server_task: Task

    def __init__(
        self,
        device_name,
        server_bind_addr="localhost",
        server_bind_port=9000,
        client_target_addr="localhost",
        client_target_port: Optional[list[int]] = None,
    ):
        super().__init__(f"{device_name}:IrqHandler")
        self._server_bind_addr = server_bind_addr
        self._server_bind_port = server_bind_port
        self._client_target_addr = client_target_addr
        if client_target_port is None:
            client_target_port = [9100, 9101, 9102, 9103]
        self._client_target_port = client_target_port
        self._callbacks = []

    def register_interrupt_handler(self, irq_msg: Irq, irq_recv_cb: Callable):
        """
        Registers a callback on the arrival of a specific interrupt.
        Cannot be done while IrqManager is running.
        """
        ev = Event()

        async def _callback():
            await ev.wait()
            irq_recv_cb()

        self._callbacks.append(_callback)
        self._msg_to_interrupt_event[irq_msg] = ev

    async def _irq_handler(self, reader: StreamReader, writer: StreamWriter):
        # pylint: disable=unused-argument
        msg = reader.read(IRQ_WIDTH)
        if not msg:
            logger.debug(self._create_message("Irq enable connection broken"))
            return
        if msg not in self._msg_to_interrupt_event:
            raise RuntimeError(f"Invalid IRQ: {msg}")
        self._msg_to_interrupt_event[msg].set()

    async def _create_server(self):
        server = await start_server(
            self._irq_handler, self._server_bind_addr, self._server_bind_port
        )
        return server

    async def send_irq_request(self, request: Irq, device: int):
        """
        Sends an IRQ request as the client.
        """
        _, writer = await open_connection(
            self._client_target_addr[device], self._client_target_port
        )
        writer.write(request.value.to_bytes(length=IRQ_WIDTH))
        await writer.drain()
        writer.close()

    async def _run(self):
        try:
            server = await self._create_server()
            self._server_task = create_task(server.serve_forever())
            await self._change_status_to_running()
            await gather(*[create_task(cb()) for cb in self._callbacks], self._server_task)
        except CancelledError:
            logger.info(self._create_message("Irq enable listener stopped"))

    async def _stop(self):
        self._server_task.cancel()
