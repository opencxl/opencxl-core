"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from typing import Callable, Awaitable

# import jsonrpcclient
# from jsonrpcclient import parse_json, request_json
# import websockets
# from websockets import WebSocketClientProtocol

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cpu import CPU
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, ADDR_TYPE, CxlMemoryHubConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import ROOT_PORT_SWITCH_TYPE
from opencxl.cxl.component.root_complex.root_complex import SystemMemControllerConfig


class CxlComplexHost(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        sys_mem_size: int,
        sys_sw_app: Callable[[], Awaitable[None]],
        user_app: Callable[[], Awaitable[None]],
        switch_host: str = "0.0.0.0",
        switch_port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._port_index = port_index
        root_ports = [RootPortClientConfig(port_index, switch_host, switch_port)]

        self._sys_mem_config = SystemMemControllerConfig(
            mem_size=sys_mem_size,
            mem_filename=f"sys-mem{port_index}.bin",
        )

        self._cxl_memory_hub_config = CxlMemoryHubConfig(
            host_name="memhub",
            root_bus=port_index,
            root_port_switch_type=ROOT_PORT_SWITCH_TYPE.PASS_THROUGH,
            root_ports=root_ports,
            sys_mem_controller=self._sys_mem_config,
        )
        self._cxl_memory_hub = CxlMemoryHub(self._cxl_memory_hub_config)

        # System Memory
        self._sys_mem_base_addr = 0xFFFF888000000000
        self._cxl_memory_hub.add_mem_range(
            self._sys_mem_base_addr, self._sys_mem_config.mem_size, ADDR_TYPE.DRAM
        )

        self._cpu = CPU(self._cxl_memory_hub, sys_sw_app, user_app)

    async def _run(self):
        tasks = [
            asyncio.create_task(self._cxl_memory_hub.run()),
        ]

        await self._cxl_memory_hub.wait_for_ready()
        tasks.append(asyncio.create_task(self._cpu.run()))
        await self._cpu.wait_for_ready()
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        tasks = [
            # asyncio.create_task(self._switch_conn_client.stop()),
            asyncio.create_task(self._cxl_memory_hub.stop()),
        ]
        await asyncio.gather(*tasks)
