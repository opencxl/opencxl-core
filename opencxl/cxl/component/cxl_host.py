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

# from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cpu import CPU
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, CxlMemoryHubConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import ROOT_PORT_SWITCH_TYPE
from opencxl.cxl.component.root_complex.root_complex import SystemMemControllerConfig
from opencxl.cxl.component.irq_manager import IrqManager


class CxlHost(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        sys_mem_size: int,
        sys_sw_app: Callable[[], Awaitable[None]],
        user_app: Callable[[], Awaitable[None]],
        host_name: str = None,
        switch_host: str = "0.0.0.0",
        switch_port: int = 8000,
        irq_host: str = "0.0.0.0",
        irq_port: int = 8500,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._port_index = port_index
        root_ports = [RootPortClientConfig(port_index, switch_host, switch_port)]
        host_name = host_name if host_name else f"CxlHostPort{port_index}"

        self._sys_mem_config = SystemMemControllerConfig(
            memory_size=sys_mem_size,
            memory_filename=f"sys-mem{port_index}.bin",
        )
        self._irq_manager = IrqManager(
            device_name=host_name,
            addr=irq_host,
            port=irq_port,
            server=True,
            device_id=port_index,
        )
        self._cxl_memory_hub_config = CxlMemoryHubConfig(
            host_name=host_name,
            root_bus=port_index,
            root_port_switch_type=ROOT_PORT_SWITCH_TYPE.PASS_THROUGH,
            root_ports=root_ports,
            sys_mem_controller=self._sys_mem_config,
            irq_handler=self._irq_manager,
        )
        self._cxl_memory_hub = CxlMemoryHub(self._cxl_memory_hub_config)
        self._cpu = CPU(self._cxl_memory_hub, sys_sw_app, user_app)

    async def _run(self):
        tasks = [
            asyncio.create_task(self._irq_manager.run()),
            asyncio.create_task(self._cxl_memory_hub.run()),
        ]
        await self._irq_manager.wait_for_ready()
        await self._cxl_memory_hub.wait_for_ready()
        tasks.append(asyncio.create_task(self._cpu.run()))
        await self._cpu.wait_for_ready()
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._cxl_memory_hub.stop()),
            asyncio.create_task(self._cpu.stop()),
            asyncio.create_task(self._irq_manager.stop()),
        ]
        await asyncio.gather(*tasks)
