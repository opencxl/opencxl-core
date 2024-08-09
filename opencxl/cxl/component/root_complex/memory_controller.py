"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from asyncio import create_task, gather
from opencxl.util.component import RunnableComponent
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MEMORY_REQUEST_TYPE,
    MemoryResponse,
    MEMORY_RESPONSE_STATUS,
)
from opencxl.util.logger import logger
from opencxl.util.accessor import FileAccessor


@dataclass
class MemoryControllerConfig:
    memory_size: int
    memory_filename: str
    host_name: str
    memory_consumer_fifos: MemoryFifoPair


class MemoryController(RunnableComponent):
    def __init__(self, config: MemoryControllerConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._memory_consumer_fifos = config.memory_consumer_fifos
        self._file_accessor = FileAccessor(config.memory_filename, config.memory_size)

    async def _process_memory_requests(self):
        while True:
            packet = await self._memory_consumer_fifos.request.get()
            if packet is None:
                logger.debug(self._create_message("Stopped processing memory access requests"))
                break

            if packet.type == MEMORY_REQUEST_TYPE.WRITE:
                await self._file_accessor.write(packet.address, packet.data, packet.size)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK)
            elif packet.type == MEMORY_REQUEST_TYPE.READ:
                data = await self._file_accessor.read(packet.address, packet.size)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK, data)
            await self._memory_consumer_fifos.response.put(response)

    async def _run(self):
        tasks = [create_task(self._process_memory_requests())]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._memory_consumer_fifos.request.put(None)
        await self._memory_consumer_fifos.response.put(None)
