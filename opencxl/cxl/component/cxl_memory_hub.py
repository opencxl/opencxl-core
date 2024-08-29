"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from dataclasses import dataclass, field
from typing import List
from enum import Enum, auto
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.root_complex.root_complex import (
    RootComplex,
    RootComplexConfig,
    SystemMemControllerConfig,
)
from opencxl.cxl.component.cache_controller import (
    CacheController,
    CacheControllerConfig,
)
from opencxl.cxl.component.root_complex.root_port_client_manager import (
    RootPortClientManager,
    RootPortClientManagerConfig,
    RootPortClientConfig,
)
from opencxl.cxl.component.root_complex.root_port_switch import (
    RootPortSwitchPortConfig,
    ROOT_PORT_SWITCH_TYPE,
)
from opencxl.cxl.component.root_complex.home_agent import MemoryRange
from opencxl.cxl.transport.cache_fifo import CacheFifoPair
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MemoryRequest,
    MEMORY_REQUEST_TYPE,
    MEMORY_RESPONSE_STATUS,
)
from opencxl.util.pci import create_bdf


class MEMORY_RANGE_TYPE(Enum):
    DRAM = auto()
    CFG = auto()
    MMIO = auto()
    CXL = auto()
    OOB = auto()


@dataclass
class CxlMemoryHubConfig:
    host_name: str
    root_bus: int
    sys_mem_controller: SystemMemControllerConfig
    root_port_switch_type: ROOT_PORT_SWITCH_TYPE
    root_ports: List[RootPortClientConfig] = field(default_factory=list)


class CxlMemoryHub(RunnableComponent):
    def __init__(self, config: CxlMemoryHubConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        self._processor_to_cache_fifo = MemoryFifoPair()
        cache_to_home_agent_fifo = CacheFifoPair()
        home_agent_to_cache_fifo = CacheFifoPair()
        cache_to_coh_bridge_fifo = CacheFifoPair()
        coh_bridge_to_cache_fifo = CacheFifoPair()

        self._memory_ranges: List[MemoryRange] = []

        # Create Root Port Client Manager
        root_port_client_manager_config = RootPortClientManagerConfig(
            client_configs=config.root_ports, host_name=config.host_name
        )
        self._root_port_client_manager = RootPortClientManager(root_port_client_manager_config)

        # Create Root Complex
        root_complex_root_ports = [
            RootPortSwitchPortConfig(
                port_index=connection.port_index, downstream_connection=connection.connection
            )
            for connection in self._root_port_client_manager.get_cxl_connections()
        ]
        root_complex_config = RootComplexConfig(
            host_name="rp",
            root_bus=config.root_bus,
            root_port_switch_type=config.root_port_switch_type,
            cache_to_home_agent_fifo=cache_to_home_agent_fifo,
            home_agent_to_cache_fifo=home_agent_to_cache_fifo,
            cache_to_coh_bridge_fifo=cache_to_coh_bridge_fifo,
            coh_bridge_to_cache_fifo=coh_bridge_to_cache_fifo,
            sys_mem_controller=config.sys_mem_controller,
            root_ports=root_complex_root_ports,
        )
        self._root_complex = RootComplex(root_complex_config)

        # if config.coh_type == COH_POLICY_TYPE.DotCache:
        if True:
            cache_to_coh_agent_fifo = cache_to_coh_bridge_fifo
            coh_agent_to_cache_fifo = coh_bridge_to_cache_fifo
        # elif config.coh_type in (COH_POLICY_TYPE.NonCache, COH_POLICY_TYPE.DotMemBI):
        #     cache_to_coh_agent_fifo = cache_to_home_agent_fifo
        #     coh_agent_to_cache_fifo = home_agent_to_cache_fifo

        cache_controller_config = CacheControllerConfig(
            component_name=config.host_name,
            processor_to_cache_fifo=self._processor_to_cache_fifo,
            cache_to_coh_agent_fifo=cache_to_coh_agent_fifo,
            coh_agent_to_cache_fifo=coh_agent_to_cache_fifo,
            cache_num_assoc=4,
            cache_num_set=8,
        )
        self._cache_controller = CacheController(cache_controller_config)

    def get_memory_ranges(self):
        return self._memory_ranges

    def add_mem_range(self, addr, size, range_type: MEMORY_RANGE_TYPE):
        self._memory_ranges.append(MemoryRange(base_addr=addr, size=size, type=range_type))

    def _get_mem_addr_type(self, addr) -> MEMORY_RANGE_TYPE:
        for range in self._memory_ranges:
            if range.base_addr <= addr < range.base_addr + range.size:
                return range.type
        return MEMORY_RANGE_TYPE.OOB

    def _cfg_addr_to_bdf(self, cfg_addr):
        return create_bdf(
            (cfg_addr >> 20) & 0xFF,  # bus bits, n = 8
            (cfg_addr >> 15) & 0x1F,
            (cfg_addr >> 12) & 0x07,
        )

    async def load(self, addr: int, size: int) -> int:
        match self._get_mem_addr_type(addr):
            case MEMORY_RANGE_TYPE.DRAM | MEMORY_RANGE_TYPE.CXL:
                packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, addr, size)
                await self._processor_to_cache_fifo.request.put(packet)
                packet = await self._processor_to_cache_fifo.response.get()
                assert packet.status == MEMORY_RESPONSE_STATUS.OK
                return packet.data
            case MEMORY_RANGE_TYPE.MMIO:
                return await self._root_complex.read_mmio(addr, size)
            case MEMORY_RANGE_TYPE.CFG:
                bdf = self._cfg_addr_to_bdf(addr)
                offset = addr & 0xFFF
                return await self._root_complex.read_config(bdf, offset, size)
            case _:
                raise Exception(self._create_message(f"Address 0x{addr:x} is OOB."))

    async def store(self, addr: int, size: int, value: int):
        match self._get_mem_addr_type(addr):
            case MEMORY_RANGE_TYPE.DRAM | MEMORY_RANGE_TYPE.CXL:
                packet = MemoryRequest(MEMORY_REQUEST_TYPE.WRITE, addr, size, value)
                await self._processor_to_cache_fifo.request.put(packet)
                packet = await self._processor_to_cache_fifo.response.get()
                assert packet.status == MEMORY_RESPONSE_STATUS.OK
            case MEMORY_RANGE_TYPE.MMIO:
                await self._root_complex.write_mmio(addr, size, value)
            case MEMORY_RANGE_TYPE.CFG:
                bdf = self._cfg_addr_to_bdf(addr)
                offset = addr & 0xFFF
                await self._root_complex.write_config(bdf, offset, size, value)
            case _:
                raise Exception(self._create_message(f"Address 0x{addr:x} is OOB."))

    def get_root_complex(self):
        return self._root_complex

    async def write_config(self, bdf: int, offset: int, size: int, value: int):
        await self._root_complex.write_config(bdf, offset, size, value)

    async def read_config(self, bdf: int, offset: int, size: int) -> int:
        return await self._root_complex.read_config(bdf, offset, size)

    async def write_mmio(self, address: int, size: int, value: int):
        await self._root_complex.write_mmio(address, size, value)

    async def read_mmio(self, address: int, size: int) -> int:
        return await self._root_complex.read_mmio(address, size)

    async def _run(self):
        run_tasks = [
            asyncio.create_task(self._root_port_client_manager.run()),
            asyncio.create_task(self._root_complex.run()),
            asyncio.create_task(self._cache_controller.run()),
        ]
        wait_tasks = [
            asyncio.create_task(self._root_port_client_manager.wait_for_ready()),
            asyncio.create_task(self._root_complex.wait_for_ready()),
            asyncio.create_task(self._cache_controller.wait_for_ready()),
        ]
        await asyncio.gather(*wait_tasks)
        await self._change_status_to_running()
        await asyncio.gather(*run_tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._root_port_client_manager.stop()),
            asyncio.create_task(self._root_complex.stop()),
            asyncio.create_task(self._cache_controller.stop()),
        ]
        await asyncio.gather(*tasks)
