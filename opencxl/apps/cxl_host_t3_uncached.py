"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio

from opencxl.util.logger import logger
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, ADDR_TYPE
from opencxl.cpu import CPU
from opencxl.apps.cxl_host import CxlHost


async def my_sys_sw_app(cxl_memory_hub: CxlMemoryHub):
    # Max addr for CFG for 0x9FFFFFFF, given max num bus = 8
    # Therefore, 0xFE000000 for MMIO does not overlap
    pci_cfg_base_addr = 0x10000000
    pci_mmio_base_addr = 0xFE000000
    cxl_hpa_base_addr = 0x100000000000
    sys_mem_base_addr = 0xFFFF888000000000

    # PCI Device
    root_complex = cxl_memory_hub.get_root_complex()
    pci_bus_driver = PciBusDriver(root_complex)
    await pci_bus_driver.init(pci_mmio_base_addr)
    pci_cfg_size = 0x10000000  # assume bus bits n = 8
    for i, device in enumerate(pci_bus_driver.get_devices()):
        cxl_memory_hub.add_mem_range(
            pci_cfg_base_addr + (i * pci_cfg_size), pci_cfg_size, ADDR_TYPE.CFG
        )
        for bar_info in device.bars:
            if bar_info.base_address == 0:
                continue
            cxl_memory_hub.add_mem_range(bar_info.base_address, bar_info.size, ADDR_TYPE.MMIO)

    # CXL Device
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, root_complex)
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, root_complex)
    await cxl_bus_driver.init()
    await cxl_mem_driver.init()
    hpa_base = cxl_hpa_base_addr
    for device in cxl_mem_driver.get_devices():
        size = device.get_memory_size()
        successful = await cxl_mem_driver.attach_single_mem_device(device, hpa_base, size)
        if not successful:
            logger.info(f"[SYS-SW] Failed to attach device {device}")
            continue
        if await device.get_bi_enable():
            cxl_memory_hub.add_mem_range(hpa_base, size, ADDR_TYPE.CXL_CACHED_BI)
        else:
            cxl_memory_hub.add_mem_range(hpa_base, size, ADDR_TYPE.CXL_UNCACHED)
        hpa_base += size

    # System Memory
    sys_mem_size = root_complex.get_sys_mem_size()
    cxl_memory_hub.add_mem_range(sys_mem_base_addr, sys_mem_size, ADDR_TYPE.DRAM)

    for range in cxl_memory_hub.get_memory_ranges():
        logger.info(
            f"[SYS-SW] MemoryRange: base: 0x{range.base_addr:X}"
            f"size: 0x{range.size:X}, type: {str(range.addr_type)}"
        )
    # TODO: Sort and merge ranges


async def sample_app(cpu: CPU):
    logger.info("[USER-APP] Starting...")
    await cpu.store(0x100000000000, 0x40, 0xDEADBEEF)
    await asyncio.sleep(0)
    val = await cpu.load(0x100000000000, 0x40)
    await asyncio.sleep(0)
    logger.info(f"0x{val:X}")
    val = await cpu.load(0x100000000040, 0x40)
    logger.info(f"0x{val:X}")


async def main():
    host = CxlHost(
        port_index=0,
        sys_mem_size=(256 * 1024 * 1024),
        sys_sw_app=my_sys_sw_app,
        user_app=sample_app,
    )
    await host.run()


if __name__ == "__main__":
    asyncio.run(main())
