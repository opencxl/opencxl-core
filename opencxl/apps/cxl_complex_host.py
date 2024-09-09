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
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, MEMORY_RANGE_TYPE, CxlMemoryHubConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import ROOT_PORT_SWITCH_TYPE
from opencxl.cxl.component.root_complex.root_complex import SystemMemControllerConfig


class CxlComplexHost(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        sys_mem_size: int,
        app: Callable[[], Awaitable[None]],
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

        self._cxl_hpa_base_addr = 0x100000000000 | (port_index << 40)
        self._sys_mem_base_addr = 0xFFFF888000000000

        # Max addr for CFG for 0x9FFFFFFF, given max num bus = 8
        # Therefore, 0xFE000000 for MMIO does not overlap
        self._pci_cfg_base_addr = 0x10000000
        self._pci_mmio_base_addr = 0xFE000000

        self._cpu = CPU(self._cxl_memory_hub, app)

    async def _init_system(self):
        # System Memory
        self._cxl_memory_hub.add_mem_range(
            self._sys_mem_base_addr, self._sys_mem_config.mem_size, MEMORY_RANGE_TYPE.DRAM
        )

        # PCI Device
        root_complex = self._cxl_memory_hub.get_root_complex()
        pci_bus_driver = PciBusDriver(root_complex)
        await pci_bus_driver.init(self._pci_mmio_base_addr)
        pci_cfg_size = 0x10000000  # assume bus bits n = 8
        for i, device in enumerate(pci_bus_driver.get_devices()):
            self._cxl_memory_hub.add_mem_range(
                self._pci_cfg_base_addr + (i * pci_cfg_size), pci_cfg_size, MEMORY_RANGE_TYPE.CFG
            )
            for bar_info in device.bars:
                if bar_info.base_address == 0:
                    continue
                self._cxl_memory_hub.add_mem_range(
                    bar_info.base_address, bar_info.size, MEMORY_RANGE_TYPE.MMIO
                )

        # CXL Device
        cxl_bus_driver = CxlBusDriver(pci_bus_driver, root_complex)
        cxl_mem_driver = CxlMemDriver(cxl_bus_driver, root_complex)
        await cxl_bus_driver.init()
        await cxl_mem_driver.init()
        hpa_base = self._cxl_hpa_base_addr
        for device in cxl_mem_driver.get_devices():
            size = device.get_memory_size()
            successful = await cxl_mem_driver.attach_single_mem_device(device, hpa_base, size)
            if not successful:
                logger.info(f"Failed to attach device {device}")
                continue
            if await device.get_bi_enable():
                self._cxl_memory_hub.add_mem_range(hpa_base, size, MEMORY_RANGE_TYPE.CXL_BI)
            else:
                self._cxl_memory_hub.add_mem_range(hpa_base, size, MEMORY_RANGE_TYPE.CXL)
            hpa_base += size

        for range in self._cxl_memory_hub.get_memory_ranges():
            logger.info(
                self._create_message(
                    f"base: 0x{range.base_addr:X}, size: 0x{range.size:X}, type: {str(range.type)}"
                )
            )

    async def _run(self):
        tasks = [
            asyncio.create_task(self._cxl_memory_hub.run()),
        ]
        # await self._switch_conn_client.wait_for_ready()
        await self._cxl_memory_hub.wait_for_ready()
        await self._init_system()
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
