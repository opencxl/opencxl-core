"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from dataclasses import dataclass
from typing import List

from opencxl.util.logger import logger
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver, PciDeviceInfo
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, MEM_ADDR_TYPE
from opencxl.cxl.component.cxl_host import CxlHost
from opencxl.cpu import CPU


@dataclass
class MemoryStruct:
    base: int
    size: int


class CxlDeviceMemTracker:
    def __init__(self, cxl_memory_hub: CxlMemoryHub):
        self._ld_tracker: dict[str, dict[MEM_ADDR_TYPE, MemoryStruct]] = {}
        self._cxl_memory_hub = cxl_memory_hub

    def _create_key(self, device_sn, device_port):
        return f"{device_sn}-{device_port}".lower()

    def _add_device(self, key):
        self._ld_tracker[key] = {k: MemoryStruct(0, 0) for k in MEM_ADDR_TYPE}

    def add_mem_range(self, device_sn, device_port, base, size, type: MEM_ADDR_TYPE):
        key = self._create_key(device_sn, device_port)
        if key not in self._ld_tracker:
            self._add_device(key)
        self._ld_tracker[key][type].base = base
        self._ld_tracker[key][type].size = size
        self._cxl_memory_hub.add_mem_range(base, size, type)

    def remove_mem_range(self, device_sn, device_port):
        key = self._create_key(device_sn, device_port)
        if key in self._ld_tracker:
            for type, mem_info in self._ld_tracker[key].items():
                if mem_info.size > 0:
                    self._cxl_memory_hub.remove_mem_range(mem_info.base, mem_info.size, type)
            del self._ld_tracker[key]
        else:
            logger.warning(f"No record for device: SN {device_sn} @ port {device_port}")

    def print(self):
        print(self._ld_tracker)


async def my_sys_sw_app(cxl_memory_hub: CxlMemoryHub):
    # Max addr for CFG is 0x9FFFFFFF, given max num bus = 8
    # Therefore, 0xFE000000 for MMIO does not overlap
    pci_cfg_base_addr = 0x10000000
    pci_mmio_base_addr = 0xFE000000
    cxl_hpa_base_addr = 0x100000000000
    sys_mem_base_addr = 0xFFFF888000000000

    # PCI Device
    mem_tracker = CxlDeviceMemTracker(cxl_memory_hub)
    root_complex = cxl_memory_hub.get_root_complex()
    pci_bus_driver = PciBusDriver(root_complex)
    await pci_bus_driver.init(pci_mmio_base_addr)

    cxl_devices: List[PciDeviceInfo] = []

    # CXL Device
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, root_complex)
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, root_complex)
    await cxl_bus_driver.init()
    await cxl_mem_driver.init()

    pci_cfg_size = 0x10000000  # assume bus bits n = 8
    cfg_base = pci_cfg_base_addr
    hpa_base = cxl_hpa_base_addr
    for device in cxl_mem_driver.get_devices():
        size = device.get_memory_size()
        successful = await cxl_mem_driver.attach_single_mem_device(device, hpa_base, size)
        sn = device.pci_device_info.serial_number
        vppb = cxl_mem_driver.get_port_number(device)
        if not successful:
            logger.info(f"[SYS-SW] Failed to attach device {device}")
            continue
        print(f"[SYS-SW] Attached to device, SN: {sn}, port: {vppb}")
        cxl_devices.append(device.pci_device_info)

        mem_tracker.add_mem_range(sn, vppb, cfg_base, pci_cfg_size, MEM_ADDR_TYPE.CFG)
        cfg_base += pci_cfg_size
        for bar_info in device.pci_device_info.bars:
            if bar_info.base_address == 0:
                continue
            mem_tracker.add_mem_range(
                sn, vppb, bar_info.base_address, bar_info.size, MEM_ADDR_TYPE.MMIO
            )

        if await device.get_bi_enable():
            mem_tracker.add_mem_range(sn, vppb, hpa_base, size, MEM_ADDR_TYPE.CXL_CACHED_BI)
        else:
            mem_tracker.add_mem_range(sn, vppb, hpa_base, size, MEM_ADDR_TYPE.CXL_UNCACHED)
        hpa_base += size

    for device in pci_bus_driver.get_devices():
        if device in cxl_devices:
            logger.warning(
                f"[SYS-SW] Skipping previously added CXL device SN: {device.serial_number}"
            )
            continue
        cxl_memory_hub.add_mem_range(cfg_base, pci_cfg_size, MEM_ADDR_TYPE.CFG)
        cfg_base += pci_cfg_size
        for bar_info in device.bars:
            if bar_info.base_address == 0:
                continue
            cxl_memory_hub.add_mem_range(bar_info.base_address, bar_info.size, MEM_ADDR_TYPE.MMIO)

    # System Memory
    sys_mem_size = root_complex.get_sys_mem_size()
    cxl_memory_hub.add_mem_range(sys_mem_base_addr, sys_mem_size, MEM_ADDR_TYPE.DRAM)

    for range in cxl_memory_hub.get_memory_ranges():
        logger.info(
            f"[SYS-SW] MemoryRange: base: 0x{range.base_addr:X} "
            f"size: 0x{range.size:X}, type: {str(range.addr_type)}"
        )
    # TODO: Sort and merge ranges


async def sample_app(_cpu: CPU, _mem_hub: CxlMemoryHub):
    logger.info("[USER-APP] Starting...")
    await _cpu.store(0x100000000000, 0x40, 0xDEADBEEF)
    val = await _cpu.load(0x100000000000, 0x40)
    logger.info(f"0x{val:X}")
    val = await _cpu.load(0x100000000040, 0x40)
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
