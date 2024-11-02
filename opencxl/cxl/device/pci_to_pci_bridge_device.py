"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from typing import cast
from asyncio import Queue, create_task, gather

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent

from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.transport.transaction import (
    CxlIoBasePacket,
    CxlMemBasePacket,
    CxlMemS2MNDRPacket,
    CxlMemS2MDRSPacket,
)


@dataclass
class BindPair:
    source: Queue
    destination: Queue


class PpbDownRouting(RunnableComponent):
    def __init__(
        self,
        downsteam_connection: CxlConnection,
        upstream_connection: CxlConnection,
        ld_id: int = 0,
        is_mld: bool = False,
    ):
        super().__init__()
        self._dsc = downsteam_connection
        self._usc = upstream_connection
        self._ld_id = ld_id
        self._is_mld = is_mld

        self._pairs = [
            BindPair(self._usc.cfg_fifo.host_to_target, self._dsc.cfg_fifo.host_to_target),
            BindPair(self._usc.mmio_fifo.host_to_target, self._dsc.mmio_fifo.host_to_target),
            BindPair(self._usc.cxl_mem_fifo.host_to_target, self._dsc.cxl_mem_fifo.host_to_target),
            BindPair(
                self._usc.cxl_cache_fifo.host_to_target, self._dsc.cxl_cache_fifo.host_to_target
            ),
        ]

    def _create_message(self, message):
        message = "ppb bind"
        return message

    async def cfg_process(self, source: Queue, destination: Queue):
        while True:
            packet = await source.get()
            if packet is None:
                break
            if self._is_mld:
                packet = cast(CxlIoBasePacket, packet)
                packet.tlp_prefix.ld_id = self._ld_id
            await destination.put(packet)

    async def mmio_process(self, source: Queue, destination: Queue):
        while True:
            packet = await source.get()
            if packet is None:
                break
            if self._is_mld:
                packet = cast(CxlIoBasePacket, packet)
                packet.tlp_prefix.ld_id = self._ld_id
            await destination.put(packet)

    async def mem_process(self, source: Queue, destination: Queue):
        while True:
            packet = await source.get()
            if packet is None:
                break
            if self._is_mld:
                packet = cast(CxlMemBasePacket, packet)
                if packet.is_m2sreq():
                    packet.m2sreq_header.ld_id = self._ld_id
                elif packet.is_m2srwd():
                    packet.m2srwd_header.ld_id = self._ld_id
                elif packet.is_s2mndr():
                    packet.s2mndr_header.ld_id = self._ld_id
                elif packet.is_s2mdrs():
                    packet.s2mdrs_header.ld_id = self._ld_id
                else:
                    logger.warning(self._create_message("Unexpected CXL.mem packet"))
            await destination.put(packet)

    async def cache_process(self, source: Queue, destination: Queue):
        while True:
            packet = await source.get()
            if packet is None:
                break
            await destination.put(packet)

    async def _run(self):
        tasks = []
        task = create_task(self.cfg_process(self._pairs[0].source, self._pairs[0].destination))
        tasks.append(task)
        task = create_task(self.mmio_process(self._pairs[1].source, self._pairs[1].destination))
        tasks.append(task)
        task = create_task(self.mem_process(self._pairs[2].source, self._pairs[2].destination))
        tasks.append(task)
        task = create_task(self.cache_process(self._pairs[3].source, self._pairs[3].destination))
        tasks.append(task)

        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        for pair in self._pairs:
            await pair.source.put(None)


class PpbUpRouting(RunnableComponent):
    def __init__(
        self,
        downsteam_connection: CxlConnection,
        upstream_connections: CxlConnection,
        is_mld: bool = False,
    ):
        super().__init__()
        self._dsc = downsteam_connection
        self._usc = upstream_connections
        self._is_mld = is_mld

        self._sources = [
            self._dsc.cfg_fifo.target_to_host,
            self._dsc.mmio_fifo.target_to_host,
            self._dsc.cxl_mem_fifo.target_to_host,
            self._dsc.cxl_cache_fifo.target_to_host,
        ]

    def _create_message(self, message):
        message = "ppb bind"
        return message

    async def cfg_process(self):
        source = self._dsc.cfg_fifo.target_to_host
        while True:
            packet = await source.get()
            if packet is None:
                break
            if self._is_mld:
                cxl_io_packet = cast(CxlIoBasePacket, packet)
                ld_id = cxl_io_packet.tlp_prefix.ld_id
                await self._usc[ld_id].cfg_fifo.target_to_host.put(packet)
            else:
                await self._usc[0].cfg_fifo.target_to_host.put(packet)

    async def mmio_process(self):
        source = self._dsc.mmio_fifo.target_to_host
        while True:
            packet = await source.get()
            if packet is None:
                break
            if self._is_mld:
                cxl_io_packet = cast(CxlIoBasePacket, packet)
                ld_id = cxl_io_packet.tlp_prefix.ld_id
                await self._usc[ld_id].mmio_fifo.target_to_host.put(packet)
            else:
                await self._usc[0].mmio_fifo.target_to_host.put(packet)

    async def mem_process(self):
        source = self._dsc.cxl_mem_fifo.target_to_host
        while True:
            packet = await source.get()
            if packet is None:
                break
            if self._is_mld:
                cxl_mem_base_packet = cast(CxlMemBasePacket, packet)
                if cxl_mem_base_packet.is_s2mndr():
                    cxl_mem_packet = cast(CxlMemS2MNDRPacket, packet)
                    ld_id = cxl_mem_packet.s2mndr_header.ld_id
                elif cxl_mem_base_packet.is_s2mdrs():
                    cxl_mem_packet = cast(CxlMemS2MDRSPacket, packet)
                    ld_id = cxl_mem_packet.s2mdrs_header.ld_id
                else:
                    raise Exception("No packet type!!!!")
                await self._usc[ld_id].cxl_mem_fifo.target_to_host.put(packet)
            else:
                await self._usc[0].cxl_mem_fifo.target_to_host.put(packet)

    async def cache_process(self):
        source = self._dsc.cxl_cache_fifo.target_to_host
        while True:
            packet = await source.get()
            if packet is None:
                break
            await self._usc[0].cxl_cache_fifo.target_to_host.put(packet)

    async def _run(self):
        tasks = []
        tasks.append(create_task(self.cfg_process()))
        tasks.append(create_task(self.mmio_process()))
        tasks.append(create_task(self.mem_process()))
        tasks.append(create_task(self.cache_process()))

        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        for source in self._sources:
            await source.put(None)


@dataclass
class EnumerationInfo:
    secondary_bus: int
    subordinate_bus: int
    memory_base: int
    memory_limit: int


class PpbDevice(RunnableComponent):
    def __init__(
        self,
        port_index: int = 0,
        ld_count: int = 1,
    ):
        super().__init__()
        self._ld_count = ld_count
        self._port_index = port_index
        self._is_mld = ld_count > 1
        self._downstream_connection = CxlConnection()
        self._upstream_connections = [CxlConnection() for i in range(ld_count)]
        self._up_routing = PpbUpRouting(
            self._downstream_connection, self._upstream_connections, self._is_mld
        )
        self._down_routings = [
            PpbDownRouting(self._downstream_connection, upstream, idx, self._is_mld)
            for (idx, upstream) in enumerate(self._upstream_connections)
        ]

    def _get_label(self) -> str:
        return f"PPB{self._port_index}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_upstream_connection(self) -> CxlConnection:
        return self._upstream_connections

    def get_downstream_connection(self) -> CxlConnection:
        return self._downstream_connection

    async def _run(self):
        logger.info(self._create_message("Starting"))
        run_tasks = [create_task(self._up_routing.run())]
        for task in self._down_routings:
            run_tasks.append(create_task(task.run()))

        wait_tasks = [create_task(self._up_routing.wait_for_ready())]
        for task in self._down_routings:
            wait_tasks.append(create_task(task.wait_for_ready()))

        # pylint: disable=duplicate-code
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)
        logger.info(self._create_message("Stopped"))

    async def _stop(self):
        logger.info(self._create_message("Stopping"))
        tasks = [
            create_task(self._up_routing.stop()),
        ]
        for task in self._down_routings:
            tasks.append(create_task(task.stop()))

        await gather(*tasks)
