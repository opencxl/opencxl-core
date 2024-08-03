"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task, run
from typing import List, cast

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.virtual_switch_manager import (
    VirtualSwitchManager,
    VirtualSwitchConfig,
)
from opencxl.cxl.component.cxl_component import PortConfig, PORT_TYPE
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.apps.cxl_complex_host import (
    CxlComplexHost,
    CxlComplexHostConfig,
    COH_POLICY_TYPE,
    ROOT_PORT_SWITCH_TYPE,
    RootComplexMemoryControllerConfig,
)
from opencxl.apps.accelerator import MyType2Accelerator
from opencxl.drivers.pci_bus_driver import PciBusDriver
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver


class TestRunner:
    def __init__(self, apps: List[RunnableComponent]):
        self._apps = apps

    async def run(self):
        tasks = []
        for app in self._apps:
            tasks.append(create_task(app.run()))
        tasks.append(create_task(self.run_test()))
        await gather(*tasks)

    async def wait_for_ready(self):
        tasks = []
        for app in self._apps:
            tasks.append(create_task(app.wait_for_ready()))
        await gather(*tasks)

    async def run_test(self):
        logger.info("Waiting for Apps to be ready")
        await self.wait_for_ready()
        host = cast(CxlComplexHost, self._apps[3])
        pci_bus_driver = PciBusDriver(host.get_root_complex())
        logger.info("Starting PCI bus driver init")
        await pci_bus_driver.init()
        logger.info("Completed PCI bus driver init")
        cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
        logger.info("Starting CXL bus driver init")
        await cxl_bus_driver.init()
        logger.info("Completed CXL bus driver init")
        cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())
        await cxl_mem_driver.init()

        hpa_base = 0x0
        next_available_hpa_base = hpa_base

        for device in cxl_mem_driver.get_devices():
            size = device.get_memory_size()
            bdf = device.pci_device_info.bdf
            bus = (bdf >> 8) & 0xFF
            logger.info(f"Scanned bus number for .mem bi-id: {bus}")
            successful = await cxl_mem_driver.attach_single_mem_device(
                device, next_available_hpa_base, size
            )
            if successful:
                host.append_dev_mmio_range(
                    device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
                )
                host.append_dev_mem_range(next_available_hpa_base, size)
                next_available_hpa_base += size
            else:
                raise Exception(
                    f"Failed to attach mem device {device.pci_device_info.get_bdf_string()}"
                )

        logger.info("Start CXL.mem Host/Device cache coherency test")
        logger.info("==============================================")


def main():
    # Set up logger
    log_file = "cxl_type2_test.log"
    log_level = "DEBUG"
    show_timestamp = True
    show_loglevel = True
    show_linenumber = True
    logger.create_log_file(
        f"logs/{log_file}",
        loglevel=log_level if log_level else "INFO",
        show_timestamp=show_timestamp,
        show_loglevel=show_loglevel,
        show_linenumber=show_linenumber,
    )

    apps = []

    switch_port = 8000
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
    ]
    sw_conn_manager = SwitchConnectionManager(port_configs, port=switch_port)
    apps.append(sw_conn_manager)

    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=sw_conn_manager, port_configs=port_configs
    )
    apps.append(physical_port_manager)

    switch_configs = [
        VirtualSwitchConfig(upstream_port_index=0, vppb_counts=4, initial_bounds=[1, 2, 3, 4])
    ]
    virtual_switch_manager = VirtualSwitchManager(
        switch_configs=switch_configs, physical_port_manager=physical_port_manager
    )
    apps.append(virtual_switch_manager)

    # 256 MB
    memory_size = 0x10000000
    host_config = CxlComplexHostConfig(
        host_name="CXLHost",
        root_bus=0,
        root_port_switch_type=ROOT_PORT_SWITCH_TYPE.PASS_THROUGH,
        root_ports=[RootPortClientConfig(0, "0.0.0.0", 8000)],
        coh_type=COH_POLICY_TYPE.DotMemBI,
        memory_controller=RootComplexMemoryControllerConfig(
            memory_size=memory_size, memory_filename="memory_dram.bin"
        ),
        memory_ranges=[],
    )
    host = CxlComplexHost(host_config)
    apps.append(host)

    accel_t2_1 = MyType2Accelerator(
        port_index=1,
        memory_size=memory_size,
        memory_file=f"mem{switch_port + 1}.bin",
        port=switch_port,
        bi_id=3,
    )
    apps.append(accel_t2_1)
    accel_t2_2 = MyType2Accelerator(
        port_index=2,
        memory_size=memory_size,
        memory_file=f"mem{switch_port + 2}.bin",
        port=switch_port,
        bi_id=4,
    )
    apps.append(accel_t2_2)
    accel_t2_3 = MyType2Accelerator(
        port_index=3,
        memory_size=memory_size,
        memory_file=f"mem{switch_port + 3}.bin",
        port=switch_port,
        bi_id=5,
    )
    apps.append(accel_t2_3)
    accel_t2_4 = MyType2Accelerator(
        port_index=4,
        memory_size=memory_size,
        memory_file=f"mem{switch_port + 4}.bin",
        port=switch_port,
        bi_id=6,
    )
    apps.append(accel_t2_4)

    test_runner = TestRunner(apps)
    run(test_runner.run())


if __name__ == "__main__":
    main()
