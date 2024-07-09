"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from asyncio import create_task, gather
from opencxl.util.component import RunnableComponent
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.memory_fifo import MemoryFifoPair
from opencxl.util.logger import logger


@dataclass
class CacheCoherencyBridgeConfig:
    upstream_cxl_cache_fifos: FifoPair
    downstream_cxl_cache_fifos: FifoPair
    memory_producer_fifos: MemoryFifoPair
    host_name: str


class CacheCoherencyBridge(RunnableComponent):
    def __init__(self, config: CacheCoherencyBridgeConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._memory_producer_fifos = config.memory_producer_fifos
        self._upstream_cxl_cache_fifos = config.upstream_cxl_cache_fifos
        self._downstream_cxl_cache_fifos = config.downstream_cxl_cache_fifos

    async def _process_upstream_host_to_target_packets(self):
        while True:
            packet = await self._upstream_cxl_cache_fifos.host_to_target.get()
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
            packet = await self._downstream_cxl_cache_fifos.target_to_host.get()
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
            create_task(self._process_upstream_host_to_target_packets()),
            create_task(self._process_downstream_target_to_host_packets()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._upstream_cxl_cache_fifos.host_to_target.put(None)
        await self._downstream_cxl_cache_fifos.target_to_host.put(None)
