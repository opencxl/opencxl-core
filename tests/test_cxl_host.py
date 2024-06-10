"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from typing import Dict, Tuple
import json
import jsonrpcserver
from jsonrpcserver import async_dispatch
from jsonrpcserver.result import ERROR_INTERNAL_ERROR
from jsonrpcclient import request_json
import websockets
import pytest

from opencxl.apps.cxl_host import CxlHostManager, CxlHost, CxlHostUtilClient
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.cxl_component import PortConfig, PORT_TYPE
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.virtual_switch_manager import (
    VirtualSwitchManager,
    VirtualSwitchConfig,
)
from opencxl.apps.cxl_type2_device_client import CxlType2DeviceClient
from opencxl.apps.single_logical_device import SingleLogicalDeviceClient

BASE_TEST_PORT = 9300


class SimpleJsonClient:
    def __init__(self, port: int, host: str = "0.0.0.0"):
        self._ws = None
        self._uri = f"ws://{host}:{port}"

    async def connect(self):
        while True:
            try:
                self._ws = await websockets.connect(self._uri)
                return
            except OSError as _:
                await asyncio.sleep(0.2)

    async def close(self):
        await self._ws.close()

    async def send(self, cmd: str):
        await self._ws.send(cmd)

    async def recv(self):
        return await self._ws.recv()

    async def send_and_recv(self, cmd: str) -> Dict:
        await self._ws.send(cmd)
        resp = await self._ws.recv()
        return json.loads(resp)


class DummyHost:
    def __init__(self):
        self._util_methods = {
            "HOST_CXL_MEM_READ": self._dummy_mem_read,
            "HOST_CXL_MEM_WRITE": self._dummy_mem_write,
            "HOST_REINIT": self._dummy_reinit,
        }
        self._ws = None
        self._event = asyncio.Event()

    def _is_valid_addr(self, addr: int) -> bool:
        return addr % 0x40 == 0

    async def _dummy_mem_read(self, addr: int) -> jsonrpcserver.Result:
        if self._is_valid_addr(addr) is False:
            return jsonrpcserver.Error(
                ERROR_INTERNAL_ERROR,
                f"Invalid Params: 0x{addr:x} is not a valid address",
            )
        return jsonrpcserver.Success({"result": addr})

    async def _dummy_mem_write(self, addr: int, data: int = None) -> jsonrpcserver.Result:
        if self._is_valid_addr(addr) is False:
            return jsonrpcserver.Error(
                ERROR_INTERNAL_ERROR,
                f"Invalid Params: 0x{addr:x} is not a valid address",
            )
        return jsonrpcserver.Success({"result": data})

    async def _dummy_reinit(self, hpa_base: int) -> jsonrpcserver.Result:
        return jsonrpcserver.Success({"result": hpa_base})

    async def conn_open(self, port: int, host: str = "0.0.0.0"):
        util_server_uri = f"ws://{host}:{port}"
        while True:
            try:
                ws = await websockets.connect(util_server_uri)
                cmd = request_json("HOST_INIT", params={"port": 0})
                await ws.send(cmd)
                resp = await ws.recv()
                self._ws = ws
                self._event.set()
                break
            except OSError as _:
                await asyncio.sleep(0.2)
        try:
            while True:
                cmd = await self._ws.recv()
                resp = await async_dispatch(cmd, methods=self._util_methods)
                await self._ws.send(resp)
        except OSError as _:
            return

    async def conn_close(self):
        await self._ws.close()

    async def wait_connected(self):
        await self._event.wait()


async def init_clients(host_port: int, util_port: int) -> Tuple[SimpleJsonClient, SimpleJsonClient]:
    util_client = SimpleJsonClient(port=util_port)
    host_client = SimpleJsonClient(port=host_port)
    await host_client.connect()
    cmd = request_json("HOST_INIT", params={"port": 0})
    resp = await host_client.send_and_recv(cmd)
    assert resp["result"]["port"] == 0
    return host_client, util_client


async def send_util_and_check_host(host_client, util_client, cmd):
    await util_client.connect()
    await util_client.send(cmd)
    cmd_recved = json.loads(await host_client.recv())
    cmd_sent = json.loads(cmd)
    cmd_sent["params"].pop("port")
    assert (
        cmd_recved["method"][5:] == cmd_sent["method"][5:]
        and cmd_recved["params"] == cmd_sent["params"]
    )


@pytest.mark.asyncio
async def test_cxl_host_manager_send_util_and_recv_host():
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_1
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_1 + 50

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    asyncio.create_task(host_manager.run())
    host_client, util_client = await init_clients(host_port, util_port)

    cmd = request_json("UTIL_CXL_MEM_READ", params={"port": 0, "addr": 0x40})
    await send_util_and_check_host(host_client, util_client, cmd)
    cmd = request_json("UTIL_CXL_MEM_WRITE", params={"port": 0, "addr": 0x40, "data": 0xA5A5})
    await send_util_and_check_host(host_client, util_client, cmd)
    cmd = request_json("UTIL_REINIT", params={"port": 0, "hpa_base": 0x40})
    await send_util_and_check_host(host_client, util_client, cmd)

    await util_client.close()
    await host_client.close()
    await host_manager.stop()


async def send_and_check_res(util_client: SimpleJsonClient, cmd: str, res_expected):
    await util_client.connect()
    await util_client.send(cmd)
    resp = await util_client.recv()
    resp = json.loads(resp)
    assert resp["result"]["result"] == res_expected


@pytest.mark.asyncio
async def test_cxl_host_manager_handle_res():
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_2
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_2 + 50

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    asyncio.create_task(host_manager.run())
    host = DummyHost()
    asyncio.create_task(host.conn_open(port=host_port))
    util_client = SimpleJsonClient(port=util_port)
    await host.wait_connected()

    addr = 0x40
    data = 0xA5A5
    cmd = request_json("UTIL_CXL_MEM_READ", params={"port": 0, "addr": addr})
    await send_and_check_res(util_client, cmd, addr)
    cmd = request_json("UTIL_CXL_MEM_WRITE", params={"port": 0, "addr": addr, "data": data})
    await send_and_check_res(util_client, cmd, data)

    await host.conn_close()
    await util_client.close()
    await host_manager.stop()


async def send_and_check_err(util_client: SimpleJsonClient, cmd: str, err_expected):
    await util_client.connect()
    await util_client.send(cmd)
    resp = await util_client.recv()
    resp = json.loads(resp)
    assert resp["error"]["message"][:14] == err_expected


@pytest.mark.asyncio
async def test_cxl_host_manager_handle_err():
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_3
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_3 + 50

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    asyncio.create_task(host_manager.run())
    dummy_host = DummyHost()
    asyncio.create_task(dummy_host.conn_open(port=host_port))
    util_client = SimpleJsonClient(port=util_port)
    await dummy_host.wait_connected()
    data = 0xA5A5
    valid_addr = 0x40
    invalid_addr = 0x41

    # Invalid USP port
    err_expected = "Invalid Params"
    cmd = request_json("UTIL_CXL_MEM_READ", params={"port": 10, "addr": valid_addr})
    await send_and_check_err(util_client, cmd, err_expected)

    # Invalid read address
    err_expected = "Invalid Params"
    cmd = request_json("UTIL_CXL_MEM_READ", params={"port": 0, "addr": invalid_addr})
    await send_and_check_err(util_client, cmd, err_expected)

    # Invalid write address
    err_expected = "Invalid Params"
    cmd = request_json("UTIL_CXL_MEM_WRITE", params={"port": 0, "addr": invalid_addr, "data": data})
    await send_and_check_err(util_client, cmd, err_expected)

    await dummy_host.conn_close()
    await util_client.close()
    await host_manager.stop()


@pytest.mark.asyncio
async def test_cxl_host_util_client():
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_4
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_4 + 50

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    asyncio.create_task(host_manager.run())
    dummy_host = DummyHost()
    asyncio.create_task(dummy_host.conn_open(port=host_port))
    await dummy_host.wait_connected()
    util_client = CxlHostUtilClient(port=util_port)

    data = 0xA5A5
    valid_addr = 0x40
    invalid_addr = 0x41
    assert valid_addr == await util_client.cxl_mem_read(0, valid_addr)
    assert data == await util_client.cxl_mem_write(0, valid_addr, data)
    assert valid_addr == await util_client.reinit(0, valid_addr)
    try:
        await util_client.cxl_mem_read(0, invalid_addr)
    except Exception as e:
        assert str(e)[:14] == "Invalid Params"

    await host_manager.stop()
    await dummy_host.conn_close()


@pytest.mark.asyncio
async def test_cxl_host_type3_ete():
    # pylint: disable=protected-access
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_5
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 50
    switch_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 51

    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
    ]
    sw_conn_manager = SwitchConnectionManager(port_configs, port=switch_port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=sw_conn_manager, port_configs=port_configs
    )

    switch_configs = [VirtualSwitchConfig(upstream_port_index=0, vppb_counts=1, initial_bounds=[1])]
    virtual_switch_manager = VirtualSwitchManager(
        switch_configs=switch_configs, physical_port_manager=physical_port_manager
    )
    sld = SingleLogicalDeviceClient(
        port_index=1,
        memory_size=0x1000000,
        memory_file=f"mem{switch_port}.bin",
        port=switch_port,
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host = CxlHost(port_index=0, switch_port=switch_port, host_port=host_port)
    test_mode_host = CxlHost(
        port_index=2, switch_port=switch_port, host_port=host_port, test_mode=True
    )

    start_tasks = [
        asyncio.create_task(host.run()),
        asyncio.create_task(host_manager.run()),
        asyncio.create_task(sw_conn_manager.run()),
        asyncio.create_task(physical_port_manager.run()),
        asyncio.create_task(virtual_switch_manager.run()),
        asyncio.create_task(sld.run()),
    ]

    wait_tasks = [
        asyncio.create_task(sw_conn_manager.wait_for_ready()),
        asyncio.create_task(physical_port_manager.wait_for_ready()),
        asyncio.create_task(virtual_switch_manager.wait_for_ready()),
        asyncio.create_task(host_manager.wait_for_ready()),
        asyncio.create_task(host.wait_for_ready()),
        asyncio.create_task(sld.wait_for_ready()),
    ]
    await asyncio.gather(*wait_tasks)

    data = 0xA5A5
    valid_addr = 0x40
    invalid_addr = 0x41
    test_tasks = [
        asyncio.create_task(host._cxl_mem_read(valid_addr)),
        asyncio.create_task(host._cxl_mem_read(invalid_addr)),
        asyncio.create_task(host._cxl_mem_write(valid_addr, data)),
        asyncio.create_task(host._cxl_mem_write(invalid_addr, data)),
        asyncio.create_task(test_mode_host._reinit()),
        asyncio.create_task(test_mode_host._reinit(valid_addr)),
        asyncio.create_task(test_mode_host._reinit(invalid_addr)),
    ]
    await asyncio.gather(*test_tasks)

    stop_tasks = [
        asyncio.create_task(sw_conn_manager.stop()),
        asyncio.create_task(physical_port_manager.stop()),
        asyncio.create_task(virtual_switch_manager.stop()),
        asyncio.create_task(host_manager.stop()),
        asyncio.create_task(host.stop()),
        asyncio.create_task(sld.stop()),
    ]
    await asyncio.gather(*stop_tasks)
    await asyncio.gather(*start_tasks)


@pytest.mark.asyncio
async def test_cxl_host_type2_ete():
    # pylint: disable=protected-access
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 52
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 53
    switch_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 54

    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
    ]
    sw_conn_manager = SwitchConnectionManager(port_configs, port=switch_port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=sw_conn_manager, port_configs=port_configs
    )

    switch_configs = [VirtualSwitchConfig(upstream_port_index=0, vppb_counts=1, initial_bounds=[1])]
    virtual_switch_manager = VirtualSwitchManager(
        switch_configs=switch_configs, physical_port_manager=physical_port_manager
    )

    type2 = CxlType2DeviceClient(
        port_index=1,
        memory_size=0x1000000,
        memory_file=f"mem{switch_port + 1}.bin",
        port=switch_port,
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host = CxlHost(port_index=0, switch_port=switch_port, host_port=host_port)
    test_mode_host = CxlHost(
        port_index=2, switch_port=switch_port, host_port=host_port, test_mode=True
    )

    start_tasks = [
        asyncio.create_task(host.run()),
        asyncio.create_task(host_manager.run()),
        asyncio.create_task(sw_conn_manager.run()),
        asyncio.create_task(physical_port_manager.run()),
        asyncio.create_task(virtual_switch_manager.run()),
        asyncio.create_task(type2.run()),
    ]

    wait_tasks = [
        asyncio.create_task(sw_conn_manager.wait_for_ready()),
        asyncio.create_task(physical_port_manager.wait_for_ready()),
        asyncio.create_task(virtual_switch_manager.wait_for_ready()),
        asyncio.create_task(host_manager.wait_for_ready()),
        asyncio.create_task(host.wait_for_ready()),
        asyncio.create_task(type2.wait_for_ready()),
    ]
    await asyncio.gather(*wait_tasks)

    data = 0xA5A5
    valid_addr = 0x40
    invalid_addr = 0x41
    test_tasks = [
        asyncio.create_task(host._cxl_mem_read(valid_addr)),
        asyncio.create_task(host._cxl_mem_read(invalid_addr)),
        asyncio.create_task(host._cxl_mem_write(valid_addr, data)),
        asyncio.create_task(host._cxl_mem_write(invalid_addr, data)),
        asyncio.create_task(test_mode_host._reinit()),
        asyncio.create_task(test_mode_host._reinit(valid_addr)),
        asyncio.create_task(test_mode_host._reinit(invalid_addr)),
    ]
    await asyncio.gather(*test_tasks)

    stop_tasks = [
        asyncio.create_task(sw_conn_manager.stop()),
        asyncio.create_task(physical_port_manager.stop()),
        asyncio.create_task(virtual_switch_manager.stop()),
        asyncio.create_task(host_manager.stop()),
        asyncio.create_task(host.stop()),
        asyncio.create_task(type2.stop()),
    ]
    await asyncio.gather(*stop_tasks)
    await asyncio.gather(*start_tasks)
