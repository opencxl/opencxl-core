"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=unused-import, duplicate-code
import asyncio
import pytest

from opencxl.apps.cxl_complex_host import CxlComplexHost, CxlComplexHostConfig
from opencxl.apps.cxl_image_classification_host import (
    CxlImageClassificationHost,
    CxlImageClassificationHostConfig,
)
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.root_complex.home_agent import MEMORY_RANGE_TYPE, MemoryRange
from opencxl.cxl.component.root_complex.root_complex import RootComplexMemoryControllerConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import (
    COH_POLICY_TYPE,
    ROOT_PORT_SWITCH_TYPE,
)
from opencxl.apps.cxl_host import CxlHostManager
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.cxl_component import PortConfig, PORT_TYPE
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.virtual_switch_manager import (
    VirtualSwitchManager,
    VirtualSwitchConfig,
)
from opencxl.apps.accelerator import MyType1Accelerator, MyType2Accelerator
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.util.component import RunnableComponent
from opencxl.util.number_const import MB
from opencxl.util.logger import logger
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver

BASE_TEST_PORT = 19300


@pytest.mark.asyncio
@pytest.mark.timeout(0)
async def test_cxl_host_type1_image_classification_host_ete():
    # pylint: disable=protected-access
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 165
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 166
    switch_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 167

    NUM_DEVS = 2

    port_configs = [PortConfig(PORT_TYPE.USP)]

    dev_list: list[MyType1Accelerator] = []

    for i in range(0, NUM_DEVS):
        port_configs.append(PortConfig(PORT_TYPE.DSP))
        dev_list.append(
            MyType1Accelerator(port_index=i + 1, port=switch_port, server_port=9050, device_id=i)
        )

    sw_conn_manager = SwitchConnectionManager(port_configs, port=switch_port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=sw_conn_manager, port_configs=port_configs
    )

    switch_configs = [
        VirtualSwitchConfig(
            upstream_port_index=0,
            vppb_counts=NUM_DEVS,
            initial_bounds=list(range(1, NUM_DEVS + 1)),
        )
    ]
    virtual_switch_manager = VirtualSwitchManager(
        switch_configs=switch_configs, physical_port_manager=physical_port_manager
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host_mem_size = 0x8000  # Needs to be big enough to test cache eviction

    host_name = "foo"
    root_port_switch_type = ROOT_PORT_SWITCH_TYPE.PASS_THROUGH
    memory_controller = RootComplexMemoryControllerConfig(host_mem_size, "foo.bin")
    root_ports = [RootPortClientConfig(0, "localhost", switch_port)]
    memory_ranges = [MemoryRange(MEMORY_RANGE_TYPE.DRAM, 0x0, host_mem_size)]

    config = CxlImageClassificationHostConfig(
        host_name,
        0,
        root_port_switch_type,
        memory_controller,
        memory_ranges,
        root_ports,
        coh_type=COH_POLICY_TYPE.DotCache,
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host = CxlImageClassificationHost(config)

    pci_bus_driver = PciBusDriver(host.get_root_complex())
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())

    start_tasks = [
        asyncio.create_task(sw_conn_manager.run()),
        asyncio.create_task(physical_port_manager.run()),
        asyncio.create_task(virtual_switch_manager.run()),
        asyncio.create_task(host_manager.run()),
    ]

    wait_tasks = [
        asyncio.create_task(sw_conn_manager.wait_for_ready()),
        asyncio.create_task(physical_port_manager.wait_for_ready()),
        asyncio.create_task(virtual_switch_manager.wait_for_ready()),
        asyncio.create_task(host_manager.wait_for_ready()),
    ]
    await asyncio.gather(*wait_tasks)

    # host
    asyncio.create_task(host.run())
    t = asyncio.create_task(host.wait_for_ready())
    await asyncio.gather(t)

    # dev
    for dev in dev_list:
        start_tasks.append(asyncio.create_task(dev.run()))
    wait_tasks = []
    for dev in dev_list:
        wait_tasks.append(asyncio.create_task(dev.wait_for_ready()))
    await asyncio.gather(*wait_tasks)

    async def test_configs():
        await pci_bus_driver.init()
        await cxl_bus_driver.init()
        await cxl_mem_driver.init()

        cache_dev_count = 0
        for device in cxl_bus_driver.get_devices():
            if device.device_dvsec:
                if device.device_dvsec.cache_capable:
                    cache_dev_count += 1
        print(f"cache_dev_count: {cache_dev_count}")
        assert cache_dev_count == NUM_DEVS
        host.set_device_count(NUM_DEVS)
        host.get_root_complex().set_cache_coh_dev_count(NUM_DEVS)

        for device in cxl_mem_driver.get_devices():
            # NOTE: The list should match the dev order
            # Not tested, though
            # otherwise the dev base may not match the IRQ ports
            host.append_dev_mmio_range(
                device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
            )

        await host.start_job()

    test_tasks = [asyncio.create_task(test_configs())]

    await asyncio.gather(*test_tasks)

    stop_tasks = [
        asyncio.create_task(sw_conn_manager.stop()),
        asyncio.create_task(physical_port_manager.stop()),
        asyncio.create_task(virtual_switch_manager.stop()),
        asyncio.create_task(host.stop()),
        asyncio.create_task(host_manager.stop()),
    ]
    for dev in dev_list:
        stop_tasks.append(asyncio.create_task(dev.stop()))

    await asyncio.gather(*stop_tasks)
    await asyncio.gather(*start_tasks)


@pytest.mark.asyncio
async def test_cxl_host_type1_complex_host_ete():
    # pylint: disable=protected-access
    host_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 165
    util_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 166
    switch_port = BASE_TEST_PORT + pytest.PORT.TEST_5 + 167

    NUM_DEVS = 4

    port_configs = [PortConfig(PORT_TYPE.USP)]

    dev_list: list[MyType1Accelerator] = []

    for i in range(0, NUM_DEVS):
        port_configs.append(PortConfig(PORT_TYPE.DSP))
        dev_list.append(
            MyType1Accelerator(
                port_index=i + 1, port=switch_port, irq_listen_port=9150 + i, device_id=i
            )
        )

    sw_conn_manager = SwitchConnectionManager(port_configs, port=switch_port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=sw_conn_manager, port_configs=port_configs
    )

    switch_configs = [
        VirtualSwitchConfig(
            upstream_port_index=0,
            vppb_counts=NUM_DEVS,
            initial_bounds=list(range(1, NUM_DEVS + 1)),
        )
    ]
    virtual_switch_manager = VirtualSwitchManager(
        switch_configs=switch_configs, physical_port_manager=physical_port_manager
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
        coh_type=COH_POLICY_TYPE.DotCache,
    )

    host_manager = CxlHostManager(host_port=host_port, util_port=util_port)
    host = CxlComplexHost(config)

    pci_bus_driver = PciBusDriver(host.get_root_complex())
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())

    start_tasks = [
        asyncio.create_task(sw_conn_manager.run()),
        asyncio.create_task(physical_port_manager.run()),
        asyncio.create_task(virtual_switch_manager.run()),
        asyncio.create_task(host_manager.run()),
        asyncio.create_task(host.run()),
    ]
    for dev in dev_list:
        start_tasks.append(asyncio.create_task(dev.run()))

    wait_tasks = [
        asyncio.create_task(sw_conn_manager.wait_for_ready()),
        asyncio.create_task(physical_port_manager.wait_for_ready()),
        asyncio.create_task(virtual_switch_manager.wait_for_ready()),
        asyncio.create_task(host_manager.wait_for_ready()),
        asyncio.create_task(host.wait_for_ready()),
    ]
    for dev in dev_list:
        wait_tasks.append(asyncio.create_task(dev.wait_for_ready()))
    await asyncio.gather(*wait_tasks)

    async def test_configs():
        await pci_bus_driver.init()
        await cxl_bus_driver.init()
        await cxl_mem_driver.init()

        cache_dev_count = 0
        for device in cxl_bus_driver.get_devices():
            if device.device_dvsec:
                if device.device_dvsec.cache_capable:
                    cache_dev_count += 1
        host.get_root_complex().set_cache_coh_dev_count(cache_dev_count)

        for device in cxl_mem_driver.get_devices():
            # NOTE: The list should match the dev order
            # Not tested, though
            # otherwise the dev base may not match the IRQ ports
            host.append_dev_mmio_range(
                device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
            )

        data = 0x00000000
        for i in range(16):
            data <<= 32
            data |= int(str(f"{i:x}") * 8, 16)

        step = 64
        for addr in range(0x00000000, 0x00010000, step):
            if addr % 0x800 == 0:
                logger.debug(f"Writing 0x{addr:x}")
            await dev_list[0]._cxl_type1_device._cache_controller.cache_coherent_store(
                addr, step, data
            )

        first_dev_rcvd = await dev_list[0]._cxl_type1_device.cxl_cache_readline(0x00000000, step)
        logger.debug(f"First device reads: {first_dev_rcvd:x}")
        assert first_dev_rcvd == data
        last_dev_rcvd = await dev_list[-1]._cxl_type1_device.cxl_cache_readline(0x00000000, step)
        logger.debug(f"Last device reads: {last_dev_rcvd:x}")
        assert last_dev_rcvd == data

        first_dev_rcvd = await dev_list[0]._cxl_type1_device.cxl_cache_readline(0x00008000, step)
        logger.debug(f"First device reads: {first_dev_rcvd:x}")
        assert first_dev_rcvd == data
        last_dev_rcvd = await dev_list[-1]._cxl_type1_device.cxl_cache_readline(0x00008000, step)
        logger.debug(f"Last device reads: {last_dev_rcvd:x}")
        assert last_dev_rcvd == data

    test_tasks = [asyncio.create_task(test_configs())]

    await asyncio.gather(*test_tasks)

    stop_tasks = [
        asyncio.create_task(sw_conn_manager.stop()),
        asyncio.create_task(physical_port_manager.stop()),
        asyncio.create_task(virtual_switch_manager.stop()),
        asyncio.create_task(host.stop()),
        asyncio.create_task(host_manager.stop()),
    ]
    for dev in dev_list:
        stop_tasks.append(asyncio.create_task(dev.stop()))
    await asyncio.gather(*stop_tasks)
    await asyncio.gather(*start_tasks)
