"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=unused-import, duplicate-code
import asyncio
import pytest

from opencxl.apps.cxl_complex_host import CxlComplexHost, CxlComplexHostConfig
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.root_complex.home_agent import MEMORY_RANGE_TYPE, MemoryRange
from opencxl.cxl.component.root_complex.root_complex import RootComplexMemoryControllerConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import ROOT_PORT_SWITCH_TYPE
from opencxl.apps.cxl_host import CxlHostManager
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.cxl_component import PortConfig, PORT_TYPE
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.virtual_switch_manager import (
    VirtualSwitchManager,
    VirtualSwitchConfig,
)
from opencxl.apps.accelerator import MyType2Accelerator
from opencxl.apps.single_logical_device import SingleLogicalDevice
from opencxl.util.number_const import MB
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver

BASE_TEST_PORT = 9300


@pytest.mark.asyncio
async def test_cxl_host_type3_complex_host_ete():
    # pylint: disable=protected-access
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 155
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 156
    switch_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 157

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

    sld = SingleLogicalDevice(
        port_index=1,
        memory_size=1024 * MB,  # min 256MB, or will cause error for DVSEC
        memory_file=f"mem{switch_port}.bin",
        port=switch_port,
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host_mem_size = 0x8000  # Needs to be big enough to test cache eviction

    host_name = "foo"
    root_port_switch_type = ROOT_PORT_SWITCH_TYPE.PASS_THROUGH
    memory_controller = RootComplexMemoryControllerConfig(host_mem_size, "foo.bin")
    root_ports = [RootPortClientConfig(0, "localhost", switch_port)]
    memory_ranges = [MemoryRange(MEMORY_RANGE_TYPE.DRAM, 0x0, host_mem_size)]

    config = CxlComplexHostConfig(
        host_name,
        0,
        root_port_switch_type,
        memory_controller,
        memory_ranges,
        root_ports,
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host = CxlComplexHost(config)

    pci_bus_driver = PciBusDriver(host.get_root_complex())
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())

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

    async def test_configs():
        await pci_bus_driver.init()
        await cxl_bus_driver.init()

    test_tasks = [
        asyncio.create_task(test_configs()),
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
