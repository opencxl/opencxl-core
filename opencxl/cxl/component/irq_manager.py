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
    sleep,
    start_server,
    open_connection,
    Lock,
)
from asyncio.exceptions import CancelledError
from enum import Enum
import traceback
from typing import Callable, Optional

from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger


class Irq(Enum):
    NULL = 0x00

    # Host-side file ready to be read by device using CXL.cache
    HOST_READY = 0x01

    # Device-side results ready to be read by host using CXL.mem
    ACCEL_VALIDATION_FINISHED = 0x02

    # Host finished writing file to device via CXL.mem
    HOST_SENT = 0x03

    # Accelerator finished training, waiting for host to send validation pics
    ACCEL_TRAINING_FINISHED = 0x04


IRQ_WIDTH = 2  # in bytes


class IrqManager(RunnableComponent):
    _msg_to_interrupt_event: dict[int, dict[Irq, Callable]]
    _callbacks: list[Callable]
    _server_task: Task

    def __init__(
        self,
        device_name,
        addr: str = "0.0.0.0",
        port: int = 9050,
        server: bool = False,
        device_id: int = 0,
    ):
        super().__init__(f"{device_name}:IrqHandler")
        self._addr = addr
        self._port = port
        self._callbacks = []
        self._msg_to_interrupt_event = {}
        self._server = server
        self._connections: list[tuple[StreamReader, StreamWriter]] = []
        self._tasks: list[Task] = []
        self._irq_handlers: list[Task] = []
        self._lock = Lock()
        self._end_signal = Event()
        self._reader_id = {}
        self._writer_id = {}
        self._device_id = device_id

    def register_interrupt_handler(self, irq_msg: Irq, irq_recv_cb: Callable, dev_id: int = 0):
        """
        Registers a callback on the arrival of a specific interrupt.
        dev_id will be locked to 0 for a client.
        """
        if not self._server:
            dev_id = 0

        async def _callback(dev_id):
            await irq_recv_cb(dev_id)

        cb_func = _callback
        print(f"Registering interrupt for IRQ {irq_msg.name} for dev {dev_id}")
        if dev_id not in self._msg_to_interrupt_event:
            self._msg_to_interrupt_event[dev_id] = {}
        self._msg_to_interrupt_event[dev_id][irq_msg] = cb_func

    async def _irq_handler(self, reader: StreamReader, writer: StreamWriter):
        print(f"Creating irq handler for dev {self._device_id}")
        while True:
            if not self._run_status:
                print("_irq_handler exiting")
                return

            msg = await reader.readexactly(IRQ_WIDTH)
            if not msg:
                logger.debug(self._create_message("Irq enable connection broken"))
                return
            msg_int = int.from_bytes(msg)
            remote_dev_id = msg_int & 0xFF
            if not self._server:
                remote_dev_id = 0
            irq_num = msg_int >> 8
            irq = Irq(irq_num)
            print(f"IRQ received for {irq.name}")
            if remote_dev_id not in self._msg_to_interrupt_event:
                raise RuntimeError(f"No IRQ registered for device: {remote_dev_id}")

            if irq not in self._msg_to_interrupt_event[remote_dev_id]:
                raise RuntimeError(f"Invalid IRQ: {irq} for device: {remote_dev_id}")

            create_task(self._msg_to_interrupt_event[remote_dev_id][irq](remote_dev_id))
            print(f"IRQ handled for {irq.name}")

    async def _create_server(self):
        self._run_status = True

        async def _new_conn(reader: StreamReader, writer: StreamWriter):
            self._connections.append((reader, writer))
            self._irq_handlers.append(create_task(self._irq_handler(reader, writer)))

        server = await start_server(_new_conn, self._addr, self._port, limit=2)
        print(f"Starting irq server on {self._addr}:{self._port}")
        return server

    async def send_irq_request(self, request: Irq, device: int = 0):
        """
        Sends an IRQ request as the client.
        """
        info = f"host sending to device {device}"
        if not self._server:
            info = f"device {self._device_id} sending to host"
        print(info)
        reader, writer = self._connections[device]
        val_w_dev_id = request.value << 8 | self._device_id
        writer.write(val_w_dev_id.to_bytes(length=IRQ_WIDTH))
        await writer.drain()

    async def start_connection(self):
        print("Device to Host IRQ Connection started!")
        reader, writer = await open_connection(self._addr, self._port, limit=2)
        self._connections.append((reader, writer))
        print("Device to Host IRQ Connection created!")
        self._run_status = True

        self._irq_handlers.append(create_task(self._irq_handler(reader, writer)))

    async def shutdown(self):
        self._run_status = False

    async def _run(self):
        try:
            if self._server:
                server = await self._create_server()
                self._server_task = create_task(server.serve_forever())

                self._tasks.append(self._server_task)
                # self._tasks.append(self._handler_task)
            else:
                # self._client_task = create_task(self._irq_handler())
                # self._tasks.append(self._client_task)
                pass
            await self._change_status_to_running()
            self._tasks.append(create_task(self._end_signal.wait()))

            await gather(*self._tasks)
            # await gather(*self._callback_tasks)
        except CancelledError:
            logger.info(self._create_message("Irq enable listener stopped"))

    async def _stop(self):
        print("IRQ Manager Stopping")
        self._end_signal.set()
        for task in self._tasks:
            task.cancel()
        print("IRQ tasks cancelled")
        for handler in self._irq_handlers:
            handler.cancel()
        print("IRQ handlers cancelled")
