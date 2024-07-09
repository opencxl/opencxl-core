"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from asyncio import create_task, gather
from enum import Enum, auto
from typing import List
from opencxl.util.component import RunnableComponent
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MEMORY_REQUEST_TYPE,
    MemoryResponse,
    MEMORY_RESPONSE_STATUS,
)
from opencxl.util.logger import logger


class MEMORY_RANGE_TYPE(Enum):
    DRAM = auto()
    CXL = auto()
    OOB = auto()


@dataclass
class MemoryRange:
    type: MEMORY_RANGE_TYPE
    base_address: int
    size: int


@dataclass
class HomeAgentConfig:
    upstream_cxl_mem_fifos: FifoPair
    downstream_cxl_mem_fifos: FifoPair
    memory_consumer_io_fifos: MemoryFifoPair
    memory_consumer_coh_fifos: MemoryFifoPair
    memory_producer_fifos: MemoryFifoPair
    host_name: str
    memory_ranges: List[MemoryRange]


# pylint: disable=duplicate-code


class HomeAgent(RunnableComponent):
    def __init__(self, config: HomeAgentConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._memory_ranges = config.memory_ranges
        self._memory_consumer_io_fifos = config.memory_consumer_io_fifos
        self._memory_consumer_coh_fifos = config.memory_consumer_coh_fifos
        self._upstream_cxl_mem_fifos = config.upstream_cxl_mem_fifos
        self._downstream_cxl_mem_fifos = config.upstream_cxl_mem_fifos

    async def _get_memory_range(self, address: int, size: int) -> MemoryRange:
        for memory_range in self._memory_ranges:
            memory_range_end = memory_range.base_address + memory_range.size - 1
            end_address = address + size - 1
            if address >= memory_range.base_address and end_address <= memory_range_end:
                return memory_range
        return MemoryRange(type=MEMORY_RANGE_TYPE.OOB, base_address=0, size=0)

    # pylint: disable=unused-argument
    async def _write_memory(self, address: int, size: int, value: int):
        # TODO: Send memory request to either CXL or DRAM
        pass

    async def _read_memory(self, address: int, size: int) -> int:
        # TODO: Send memory request to either CXL or DRAM
        return 0

    # pylint: enable=unused-argument

    async def _process_memory_io_bridge_requests(self):
        while True:
            packet = await self._memory_consumer_io_fifos.request.get()
            if packet is None:
                logger.debug(
                    self._create_message("Stopped processing memory access requests from IO Bridge")
                )
                break
            if packet.type == MEMORY_REQUEST_TYPE.WRITE:
                self._write_memory(packet.address, packet.size, packet.data)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK)
                await self._memory_consumer_io_fifos.response.put(response)
            elif packet.type == MEMORY_REQUEST_TYPE.READ:
                data = self._read_memory(packet.address, packet.size)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK, data)
                await self._memory_consumer_io_fifos.response.put(response)

    async def _process_memory_coh_bridge_requests(self):
        while True:
            packet = await self._memory_consumer_coh_fifos.request.get()
            if packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing memory access requests from Cache Coherency Bridge"
                    )
                )
                break
            if packet.type == MEMORY_REQUEST_TYPE.WRITE:
                self._write_memory(packet.address, packet.size, packet.data)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK)
                await self._memory_consumer_coh_fifos.response.put(response)
            elif packet.type == MEMORY_REQUEST_TYPE.READ:
                data = self._read_memory(packet.address, packet.size)
                data = 0
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK, data)
                await self._memory_consumer_coh_fifos.response.put(response)

    async def _process_upstream_host_to_target_packets(self):
        while True:
            packet = await self._upstream_cxl_mem_fifos.host_to_target.get()
            if packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing upstream host to target CXL.mem packets"
                    )
                )
                break
            # TODO: Process upstream host to target CXL.mem packets

    async def _process_downstream_target_to_host_packets(self):
        while True:
            packet = await self._downstream_cxl_mem_fifos.target_to_host.get()
            if packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing downstream target to host CXL.mem packets"
                    )
                )
                break
            # TODO: Process downstream target to host CXL.mem packets

    async def _run(self):
        tasks = [
            create_task(self._process_memory_io_bridge_requests()),
            create_task(self._process_memory_coh_bridge_requests()),
            create_task(self._process_upstream_host_to_target_packets()),
            create_task(self._process_downstream_target_to_host_packets()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._memory_consumer_io_fifos.put(None)
        await self._memory_consumer_coh_fifos.put(None)
        await self._upstream_cxl_mem_fifos.host_to_target.put(None)
        await self._downstream_cxl_mem_fifos.target_to_host.put(None)


# pylint: enable=duplicate-code
