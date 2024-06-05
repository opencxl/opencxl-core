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
from opencxl.cxl.device.root_port_device import CxlRootPortDevice
from opencxl.cxl.component.switch_connection_client import SwitchConnectionClient
from opencxl.cxl.component.host_manager_conn import (
    HostManagerConnClient,
    HostConnServer,
    UtilConnServer,
    Result,
)
from opencxl.cxl.component.cxl_component import CXL_COMPONENT_TYPE


class CxlHost(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        no_hm: bool = False,
        switch_host: str = "0.0.0.0",
        switch_port: int = 8000,
        host_host: str = "0.0.0.0",
        host_port: int = 8300,
        test_mode: bool = False,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._test_mode = test_mode
        self._switch_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.R, host=switch_host, port=switch_port
        )
        self._methods = {
            "HOST_CXL_MEM_READ": self._cxl_mem_read,
            "HOST_CXL_MEM_WRITE": self._cxl_mem_write,
            "HOST_REINIT": self._reinit,
        }
        if not no_hm:
            self._host_manager_conn_client = HostManagerConnClient(
                port_index=port_index, host=host_host, port=host_port, methods=self._methods
            )
        else:
            logger.debug(
                "[CXLHost] HostManagerConnClient is not starting because of the --no-hm arg."
            )
        self._root_port_device = CxlRootPortDevice(
            downstream_connection=self._switch_conn_client.get_cxl_connection(),
            label=label,
            test_mode=self._test_mode,
        )
        self._port_index = port_index
        self._no_hm = no_hm

    def _is_valid_addr(self, addr: int) -> bool:
        return 0 <= addr <= self._root_port_device.get_used_hpa_size() and (addr % 0x40 == 0)

    async def _cxl_mem_read(self, addr: int) -> Result:
        logger.info(self._create_message(f"CXL.mem Read: addr=0x{addr:x}"))
        if self._is_valid_addr(addr) is False:
            logger.error(
                self._create_message(f"CXL.mem Read: Error - 0x{addr:x} is not a valid address")
            )
            return Result(f"Invalid Params: 0x{addr:x} is not a valid address")
        op_addr = addr + self._root_port_device.get_hpa_base()
        res = await self._root_port_device.cxl_mem_read(op_addr)
        return Result(res)

    async def _cxl_mem_write(self, addr: int, data: int) -> Result:
        logger.info(self._create_message(f"CXL.mem Write: addr=0x{addr:x} data=0x{data:x}"))
        if self._is_valid_addr(addr) is False:
            logger.error(
                self._create_message(f"CXL.mem Write: Error - 0x{addr:x} is not a valid address")
            )
            return Result(f"Invalid Params: 0x{addr:x} is not a valid address")
        op_addr = addr + self._root_port_device.get_hpa_base()
        res = await self._root_port_device.cxl_mem_write(op_addr, data)
        return Result(res)

    async def _reinit(self, hpa_base: int = None) -> Result:
        logger.info(self._create_message(f"Reinit: hpa_base={hpa_base}"))
        if hpa_base is None:
            hpa_base = self._root_port_device.get_hpa_base()
        elif hpa_base % 0x40:
            return Result("Invalid Params: HPA Base must be a multiple of 0x40")
        await self._root_port_device.init(hpa_base)
        return Result(hpa_base)

    async def _run(self):
        tasks = [
            asyncio.create_task(self._switch_conn_client.run()),
            asyncio.create_task(self._root_port_device.run()),
        ]
        if not self._no_hm:
            tasks.append(asyncio.create_task(self._host_manager_conn_client.run()))
        await self._switch_conn_client.wait_for_ready()
        await self._root_port_device.wait_for_ready()
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._switch_conn_client.stop()),
            asyncio.create_task(self._root_port_device.stop()),
        ]
        if not self._no_hm:
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

    async def reinit(self, port: int, hpa_base: int = None) -> str:
        logger.info(f"CXL-Host[Port{port}]: Start CXL-Host Reinit")
        cmd = request_json("UTIL_REINIT", params={"port": port, "hpa_base": hpa_base})
        return await self._process_cmd(cmd)
