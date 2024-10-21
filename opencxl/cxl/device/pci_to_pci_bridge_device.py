"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from typing import Optional
from asyncio import Queue, create_task, gather

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent

from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.component.cxl_bridge_component import (
    CxlDownstreamPortComponent,
)
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
from opencxl.cxl.device.port_device import CxlPortDevice
from opencxl.cxl.config_space.port import (
    CxlDownstreamPortConfigSpace,
    CxlDownstreamPortConfigSpaceOptions,
)
from opencxl.cxl.config_space.dvsec import (
    DvsecConfigSpaceOptions,
    DvsecRegisterLocatorOptions,
    CXL_DEVICE_TYPE,
)
from opencxl.pci.component.pci import (
    PciComponentIdentity,
    PciBridgeComponent,
    PCI_BRIDGE_TYPE,
    PCI_CLASS,
    PCI_BRIDGE_SUBCLASS,
    PCI_DEVICE_PORT_TYPE,
    EEUM_VID,
    SW_DSP_DID,
)
from opencxl.pci.config_space.pci import REG_ADDR
from opencxl.pci.component.mmio_manager import MmioManager, BarEntry
from opencxl.pci.component.config_space_manager import (
    ConfigSpaceManager,
    PCI_DEVICE_TYPE,
)


@dataclass
class BindPair:
    source: Queue
    destination: Queue


class BindProcessor(RunnableComponent):
    def __init__(
        self,
        downsteam_connection: CxlConnection,
        upstream_connection: CxlConnection,
    ):
        super().__init__()
        self._dsc = downsteam_connection
        self._usc = upstream_connection

        self._pairs = [
            BindPair(self._dsc.cfg_fifo.host_to_target, self._usc.cfg_fifo.host_to_target),
            BindPair(self._usc.cfg_fifo.target_to_host, self._dsc.cfg_fifo.target_to_host),
            BindPair(self._dsc.mmio_fifo.host_to_target, self._usc.mmio_fifo.host_to_target),
            BindPair(self._usc.mmio_fifo.target_to_host, self._dsc.mmio_fifo.target_to_host),
            BindPair(
                self._dsc.cxl_mem_fifo.host_to_target,
                self._usc.cxl_mem_fifo.host_to_target,
            ),
            BindPair(
                self._usc.cxl_mem_fifo.target_to_host,
                self._dsc.cxl_mem_fifo.target_to_host,
            ),
            BindPair(
                self._dsc.cxl_cache_fifo.host_to_target,
                self._usc.cxl_cache_fifo.host_to_target,
            ),
            BindPair(
                self._usc.cxl_cache_fifo.target_to_host,
                self._dsc.cxl_cache_fifo.target_to_host,
            ),
        ]

    def _create_message(self, message):
        message = "ppb bind"
        return message

    async def _process(self, source: Queue, destination: Queue):
        while True:
            packet = await source.get()
            if packet is None:
                break
            await destination.put(packet)

    async def _run(self):
        tasks = []
        for pair in self._pairs:
            task = create_task(self._process(pair.source, pair.destination))
            tasks.append(task)
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        for pair in self._pairs:
            await pair.source.put(None)


@dataclass
class EnumerationInfo:
    secondary_bus: int
    subordinate_bus: int
    memory_base: int
    memory_limit: int


class PPBDevice(RunnableComponent):
    def __init__(
        self,
        port_index: int = 0,
    ):

        super().__init__()
        self._port_index = port_index
        self._downstream_connection = CxlConnection()
        self._upstream_connection = CxlConnection()
        self._bind_processor = BindProcessor(self._upstream_connection, self._downstream_connection)

    def _get_label(self) -> str:
        return f"PPB{self._port_index}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_upstream_connection(self) -> CxlConnection:
        return self._upstream_connection

    def get_downstream_connection(self) -> CxlConnection:
        return self._downstream_connection

    async def _run(self):
        logger.info(self._create_message("Starting"))
        run_tasks = [
            create_task(self._bind_processor.run()),
        ]
        wait_tasks = [
            create_task(self._bind_processor.wait_for_ready()),
        ]
        # pylint: disable=duplicate-code
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)
        logger.info(self._create_message("Stopped"))

    async def _stop(self):
        logger.info(self._create_message("Stopping"))
        tasks = [
            create_task(self._bind_processor.stop()),
        ]
        await gather(*tasks)
