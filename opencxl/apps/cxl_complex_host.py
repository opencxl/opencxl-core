"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from dataclasses import dataclass, field
from typing import List
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.root_complex.root_complex import (
    RootComplex,
    RootComplexConfig,
    RootComplexMemoryControllerConfig,
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


@dataclass
class CxlComplexHostConfig:
    host_name: str
    root_bus: int
    root_port_switch_type: ROOT_PORT_SWITCH_TYPE
    memory_controller: RootComplexMemoryControllerConfig
    root_ports: List[RootPortClientConfig] = field(default_factory=list)
    memory_ranges: List[MemoryRange] = field(default_factory=list)


class CxlComplexHost(RunnableComponent):
    def __init__(self, config: CxlComplexHostConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

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
            root_port_switch_type=config.root_port_switch_type,
            host_name=config.host_name,
            root_bus=config.root_bus,
            root_ports=root_complex_root_ports,
            memory_ranges=[],
            memory_controller=config.memory_controller,
        )
        self._root_complex = RootComplex(root_complex_config)

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
        ]
        wait_tasks = [
            asyncio.create_task(self._root_port_client_manager.wait_for_ready()),
            asyncio.create_task(self._root_complex.wait_for_ready()),
        ]
        await asyncio.gather(*wait_tasks)
        await self._change_status_to_running()
        await asyncio.gather(*run_tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._root_port_client_manager.stop()),
            asyncio.create_task(self._root_complex.stop()),
        ]
        await asyncio.gather(*tasks)
