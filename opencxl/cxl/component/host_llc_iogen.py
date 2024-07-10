"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task
from dataclasses import dataclass
from random import randrange

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MemoryRequest,
    MEMORY_REQUEST_TYPE,
    MEMORY_RESPONSE_STATUS,
)


@dataclass
class HostLlcIoGenConfig:
    host_name: str
    processor_to_cache_fifo: MemoryFifoPair


class HostLlcIoGen(RunnableComponent):
    def __init__(self, config: HostLlcIoGenConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._host_name = config.host_name
        self._processor_to_cache_fifo = config.processor_to_cache_fifo

        self._internal_iogen = False

    # pylint: disable=duplicate-code
    async def load(self, address: int, size: int) -> int:
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, address, size)
        await self._processor_to_cache_fifo.request.put(packet)
        packet = await self._processor_to_cache_fifo.response.get()

        assert packet.status == MEMORY_RESPONSE_STATUS.OK
        return packet.data

    async def store(self, address: int, size: int, value: int):
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.WRITE, address, size, value)
        await self._processor_to_cache_fifo.request.put(packet)
        packet = await self._processor_to_cache_fifo.response.get()

        assert packet.status == MEMORY_RESPONSE_STATUS.OK

    async def _host_process_main(self):
        if self._internal_iogen is True:

            valid_addr = set()
            for _ in range(5000):
                addr = randrange(0, 0x1000) * 0x40
                written_data = addr
                valid_addr.add(addr)

                await self.store(addr, 64, written_data)
                logger.debug(f"[{self._host_name}] Write 0x{written_data:X} at 0x{addr:x}")

            logger.info(f"[{self._host_name}] Written Counts {len(valid_addr)}")

            for _, addr in enumerate(valid_addr):
                read_data = await self.load(addr, 64)
                logger.debug(f"[{self._host_name}] Read 0x{read_data:X} from 0x{addr:x}")
                assert addr == read_data, f"addr={hex(addr)}:data={hex(read_data)}"

            logger.info(f"[{self._host_name}] Simple Test Done")

    async def _run(self):
        create_task(self._host_process_main())
        await self._change_status_to_running()

    async def _stop(self):
        await self._processor_to_cache_fifo.request.put(None)
