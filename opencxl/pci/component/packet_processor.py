"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from abc import abstractmethod
from asyncio import create_task, gather
from typing import Optional

from opencxl.util.logger import logger
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.util.component import RunnableComponent


class PacketProcessor(RunnableComponent):
    def __init__(
        self,
        upstream_fifo: FifoPair,
        downstream_fifo: Optional[FifoPair] = None,
        label: Optional[str] = None,
    ):
        super().__init__()
        self._label = label
        self._upstream_fifo = upstream_fifo
        self._downstream_fifo = downstream_fifo

    @abstractmethod
    async def _process_host_to_target(self):
        pass

    async def _process_target_to_host(self):
        if self._downstream_fifo is None:
            logger.debug(self._create_message("Skipped processing downstream outgoing fifo"))
            return
        logger.debug(self._create_message("Started processing outgoing fifo"))
        while True:
            packet = await self._downstream_fifo.target_to_host.get()
            if packet is None:
                logger.debug(self._create_message("Stopped processing outgoing fifo"))
                break
            await self._upstream_fifo.target_to_host.put(packet)

    async def _run(self):
        tasks = [
            create_task(self._process_host_to_target()),
            create_task(self._process_target_to_host()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        if self._downstream_fifo is not None:
            await self._downstream_fifo.target_to_host.put(None)
        await self._upstream_fifo.host_to_target.put(None)
