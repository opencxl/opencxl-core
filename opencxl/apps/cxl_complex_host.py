"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
import jsonrpcclient
from jsonrpcclient import parse_json, request_json
import websockets
from websockets import WebSocketClientProtocol

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.switch_connection_client import SwitchConnectionClient
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cpu import CPU
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, MEMORY_RANGE_TYPE


class CxlComplexHost(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        app,
        switch_host: str = "0.0.0.0",
        switch_port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._port_index = port_index
        self._switch_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.R, host=switch_host, port=switch_port
        )
        self._cpu = CPU()
        self._cxl_memory_hub = CxlMemoryHub()
        self._cxl_hpa_base_addr = 0x100000000000 | (port_index << 40)
        self._system_memory_base_addr = 0xFFFF888000000000
        self._pci_mmio_base_addr = 0xFE00000000
        self._pci_cfg_base_addr = 0x10000000

    async def _init_system(self, cxl_hpa_base_addr):
        root_complex = self._cxl_memory_hub.get_root_complex()
        pci_bus_driver = PciBusDriver(root_complex)
        cxl_bus_driver = CxlBusDriver(pci_bus_driver, root_complex)
        cxl_mem_driver = CxlMemDriver(cxl_bus_driver, root_complex)
        await pci_bus_driver.init()
        await cxl_bus_driver.init()
        await cxl_mem_driver.init()

        # System Memory
        self._cxl_memory_hub.add_mem_range(
            self._system_memory_base_addr, size, MEMORY_RANGE_TYPE.DRAM
        )

        # PCI Device
        await pci_bus_driver.init(self._pci_mmio_base_addr)
        pci_cfg_size = 0x10000000  # assume bus bits n = 8
        for i, device in enumerate(pci_bus_driver.get_devices()):
            self._cxl_memory_hub.add_mem_range(
                self._pci_cfg_base_addr + (i * pci_cfg_size), pci_cfg_size, MEMORY_RANGE_TYPE.CFG
            )
            for bar_info in device.bars:
                self._cxl_memory_hub.add_mem_range(
                    bar_info.base_address, bar_info.size, MEMORY_RANGE_TYPE.MMIO
                )

        # CXL Devices
        dev_count = 0
        hpa_base = cxl_hpa_base_addr
        for device in cxl_mem_driver.get_devices():
            size = device.get_memory_size()
            successful = await cxl_mem_driver.attach_single_mem_device(device, hpa_base, size)
            if successful:
                self._cxl_memory_hub.add_mem_range(hpa_base, size, MEMORY_RANGE_TYPE.CXL)
                hpa_base += size
                dev_count += 1

    def _is_valid_addr(self, addr: int) -> bool:
        return 0 <= addr <= self._root_port_device.get_used_hpa_size() and (addr % 0x40 == 0)

    async def _run(self):
        tasks = [
            asyncio.create_task(self._switch_conn_client.run()),
        ]
        await self._switch_conn_client.wait_for_ready()
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._switch_conn_client.stop()),
            asyncio.create_task(self._root_port_device.stop()),
        ]
        if self._hm_mode:
            tasks.append(asyncio.create_task(self._host_manager_conn_client.stop()))
        await asyncio.gather(*tasks)


class CxlHostManager(RunnableComponent):
    def __init__(
        self,
        host_host: str = "0.0.0.0",
        host_port: int = 8300,
        util_host: str = "0.0.0.0",
        util_port: int = 8400,
    ):
        super().__init__()
        self._host_connections = {}
        self._host_conn_server = HostConnServer(host_host, host_port, self._set_host_conn_callback)
        self._util_conn_server = UtilConnServer(util_host, util_port, self._get_host_conn_callback)

    async def _set_host_conn_callback(self, port: int, ws) -> WebSocketClientProtocol:
        self._host_connections[port] = ws

    async def _get_host_conn_callback(self, port: int) -> WebSocketClientProtocol:
        return self._host_connections.get(port)

    async def _run(self):
        tasks = [
            asyncio.create_task(self._host_conn_server.run()),
            asyncio.create_task(self._util_conn_server.run()),
        ]
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._host_conn_server.stop()),
            asyncio.create_task(self._util_conn_server.stop()),
        ]
        await asyncio.gather(*tasks)


class CxlHostUtilClient:
    def __init__(self, host: str = "0.0.0.0", port: int = 8400):
        self._uri = f"ws://{host}:{port}"

    async def _process_cmd(self, cmd: str) -> str:
        async with websockets.connect(self._uri) as ws:
            logger.debug(f"Issuing: {cmd}")
            await ws.send(str(cmd))
            resp = await ws.recv()
            logger.debug(f"Received: {resp}")
            resp = parse_json(resp)
            match resp:
                case jsonrpcclient.Ok(result, _):
                    return result["result"]
                case jsonrpcclient.Error(_, err, _, _):
                    raise Exception(f"{err}")

    async def cxl_mem_write(self, port: int, addr: int, data: int) -> str:
        logger.info(f"CXL-Host[Port{port}]: Start CXL.mem Write: addr=0x{addr:x} data=0x{data:x}")
        cmd = request_json("UTIL_CXL_MEM_WRITE", params={"port": port, "addr": addr, "data": data})
        return await self._process_cmd(cmd)

    async def cxl_mem_read(self, port: int, addr: int) -> str:
        logger.info(f"CXL-Host[Port{port}]: Start CXL.mem Read: addr=0x{addr:x}")
        cmd = request_json("UTIL_CXL_MEM_READ", params={"port": port, "addr": addr})
        return await self._process_cmd(cmd)

    async def cxl_mem_birsp(
        self, port: int, opcode: CXL_MEM_M2SBIRSP_OPCODE, bi_id: int = 0, bi_tag: int = 0
    ) -> str:
        logger.info(
            f"CXL-Host[Port{port}]: Start CXL.mem BIRsp: opcode: 0x{opcode:x}"
            f" id: {bi_id}, tag: {bi_tag}"
        )
        cmd = request_json(
            "UTIL_CXL_MEM_BIRSP",
            params={"port": port, "opcode": opcode, "bi_id": bi_id, "bi_tag": bi_tag},
        )
        return await self._process_cmd(cmd)

    async def reinit(self, port: int, hpa_base: int = None) -> str:
        logger.info(f"CXL-Host[Port{port}]: Start CXL-Host Reinit")
        cmd = request_json("UTIL_REINIT", params={"port": port, "hpa_base": hpa_base})
        return await self._process_cmd(cmd)
