"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, List
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.root_complex.root_complex import (
    RootComplex,
    RootComplexConfig,
    RootComplexMemoryControllerConfig,
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
    COH_POLICY_TYPE,
    ROOT_PORT_SWITCH_TYPE,
)
from opencxl.cxl.component.root_complex.home_agent import MemoryRange
from opencxl.cxl.transport.memory_fifo import MemoryFifoPair
from opencxl.cxl.transport.cache_fifo import CacheFifoPair
from opencxl.cxl.component.host_llc_iogen import (
    HostLlcIoGen,
    HostLlcIoGenConfig,
)


@dataclass
class CxlComplexHostConfig:
    host_name: str
    root_bus: int
    root_port_switch_type: ROOT_PORT_SWITCH_TYPE
    memory_controller: RootComplexMemoryControllerConfig
    memory_ranges: List[MemoryRange] = field(default_factory=list)
    root_ports: List[RootPortClientConfig] = field(default_factory=list)
    coh_type: Optional[COH_POLICY_TYPE] = COH_POLICY_TYPE.NonCache


class CxlComplexHost(RunnableComponent):
    def __init__(self, config: CxlComplexHostConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        processor_to_cache_fifo = MemoryFifoPair()
        cache_to_home_agent_fifo = CacheFifoPair()
        home_agent_to_cache_fifo = CacheFifoPair()
        cache_to_coh_bridge_fifo = CacheFifoPair()
        coh_bridge_to_cache_fifo = CacheFifoPair()

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
            host_name=config.host_name,
            root_bus=config.root_bus,
            root_port_switch_type=config.root_port_switch_type,
            cache_to_home_agent_fifo=cache_to_home_agent_fifo,
            home_agent_to_cache_fifo=home_agent_to_cache_fifo,
            cache_to_coh_bridge_fifo=cache_to_coh_bridge_fifo,
            coh_bridge_to_cache_fifo=coh_bridge_to_cache_fifo,
            memory_controller=config.memory_controller,
            memory_ranges=config.memory_ranges,
            root_ports=root_complex_root_ports,
            coh_type=config.coh_type,
        )
        self._root_complex = RootComplex(root_complex_config)

        if config.coh_type == COH_POLICY_TYPE.DotCache:
            cache_to_coh_agent_fifo = cache_to_coh_bridge_fifo
            coh_agent_to_cache_fifo = coh_bridge_to_cache_fifo
        elif config.coh_type in (COH_POLICY_TYPE.NonCache, COH_POLICY_TYPE.DotMemBI):
            cache_to_coh_agent_fifo = cache_to_home_agent_fifo
            coh_agent_to_cache_fifo = home_agent_to_cache_fifo

        cache_controller_config = CacheControllerConfig(
            component_name=config.host_name,
            processor_to_cache_fifo=processor_to_cache_fifo,
            cache_to_coh_agent_fifo=cache_to_coh_agent_fifo,
            coh_agent_to_cache_fifo=coh_agent_to_cache_fifo,
            cache_num_assoc=4,
            cache_num_set=8,
        )
        self._cache_controller = CacheController(cache_controller_config)

        host_processor_config = HostLlcIoGenConfig(
            host_name=config.host_name,
            processor_to_cache_fifo=processor_to_cache_fifo,
            memory_size=config.memory_controller.memory_size,
        )
        self._host_simple_processor = HostLlcIoGen(host_processor_config)

        self._dev_mmio_ranges: list[tuple[int, int]] = []
        self._dev_mem_ranges: list[tuple[int, int]] = []

    def append_dev_mmio_range(self, base, size):
        self._dev_mmio_ranges.append((base, size))

    def append_dev_mem_range(self, base, size):
        self._dev_mem_ranges.append((base, size))

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
            asyncio.create_task(self._host_simple_processor.run()),
        ]
        wait_tasks = [
            asyncio.create_task(self._root_port_client_manager.wait_for_ready()),
            asyncio.create_task(self._root_complex.wait_for_ready()),
            asyncio.create_task(self._cache_controller.wait_for_ready()),
            asyncio.create_task(self._host_simple_processor.wait_for_ready()),
        ]
        await asyncio.gather(*wait_tasks)
        await self._change_status_to_running()
        await asyncio.gather(*run_tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._root_port_client_manager.stop()),
            asyncio.create_task(self._root_complex.stop()),
            asyncio.create_task(self._cache_controller.stop()),
            asyncio.create_task(self._host_simple_processor.stop()),
        ]
        await asyncio.gather(*tasks)
