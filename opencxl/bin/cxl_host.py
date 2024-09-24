"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
import click
from opencxl.util.logger import logger
from opencxl.cxl.environment import parse_cxl_environment
from opencxl.cxl.component.cxl_component import PORT_TYPE
from opencxl.cxl.component.root_complex.root_complex import RootComplexMemoryControllerConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import (
    ROOT_PORT_SWITCH_TYPE,
)
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.cache_controller import MemoryRange, ADDR_TYPE
from opencxl.apps.cxl_host import CxlHost, CxlHostConfig
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver


@click.group(name="host")
def host_group():
    """Command group for managing CXL Complex Host"""
    pass


async def run_host_group(hosts):
    for host in hosts:
        pci_bus_driver = PciBusDriver(host.get_root_complex())
        cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
        cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())

        start_tasks = [
            asyncio.create_task(host.run()),
        ]
        wait_task = asyncio.create_task(host.wait_for_ready())
        await asyncio.gather(wait_task)

        await pci_bus_driver.init()
        await cxl_bus_driver.init()
        await cxl_mem_driver.init()

        cache_dev_count = 0
        for device in cxl_bus_driver.get_devices():
            if device.device_dvsec:
                if device.device_dvsec.cache_capable:
                    cache_dev_count += 1
        logger.info(f"cache_dev_count: {cache_dev_count}")

        hpa_base = 0x2900000000
        dev_count = 0
        next_available_hpa_base = hpa_base
        for device in cxl_mem_driver.get_devices():
            size = device.get_memory_size()
            successful = await cxl_mem_driver.attach_single_mem_device(
                device, next_available_hpa_base, size
            )
            if successful:
                host.append_dev_mmio_range(
                    device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
                )
                host.append_dev_mem_range(next_available_hpa_base, size)
                next_available_hpa_base += size
                dev_count += 1

        # host.set_device_count(cache_dev_count)
        # host.get_root_complex().set_cache_coh_dev_count(cache_dev_count)

        # for device in cxl_mem_driver.get_devices():
        #     # NOTE: The list should match the dev order
        #     # otherwise the dev base may not match the IRQ ports
        #     host.append_dev_mmio_range(
        #         device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
        #     )
    while True:
        await asyncio.sleep(0.1)


def start_group(config_file: str):
    logger.info(f"Starting CXL Host Group - Config: {config_file}")

    try:
        environment = parse_cxl_environment(config_file)
    except Exception as e:
        logger.error(f"Failed to parse environment configuration: {e}")
        return

    hosts = []
    for _, host_config in enumerate(environment.host_configs):
        print(host_config)
        hosts.append(CxlHost(host_config))
    asyncio.run(run_host_group(hosts))
