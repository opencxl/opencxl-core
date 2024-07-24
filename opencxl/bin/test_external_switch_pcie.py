import asyncio
from opencxl.apps.cxl_complex_host import (
    CxlComplexHost,
    CxlComplexHostConfig,
    RootPortClientConfig,
    ROOT_PORT_SWITCH_TYPE,
    RootComplexMemoryControllerConfig,
)
from opencxl.apps.pci_device import PciDevice
from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.drivers.pci_bus_driver import PciBusDriver
from typing import List, cast


class TestRunner:
    def __init__(self, apps: List[RunnableComponent]):
        self._apps = apps

    async def run(self):
        tasks = []
        for app in self._apps:
            tasks.append(asyncio.create_task(app.run()))
        tasks.append(asyncio.create_task(self.run_test()))
        await asyncio.gather(*tasks)

    async def wait_for_ready(self):
        tasks = []
        for app in self._apps:
            tasks.append(asyncio.create_task(app.wait_for_ready()))
        await asyncio.gather(*tasks)

    async def run_test(self):
        logger.info("Waiting for Apps to be ready")
        await self.wait_for_ready()
        host = cast(CxlComplexHost, self._apps[0])
        bus_driver = PciBusDriver(host.get_root_complex())
        logger.info("Starting PCI bus driver init")
        await bus_driver.init()
        logger.info("Completed PCI bus driver init")
        # logger.info("Starting PCI bus driver init")
        # await bus_driver.init()
        # logger.info("Completed PCI bus driver init")
        # logger.info("Starting PCI bus driver init")
        # await bus_driver.init()
        # logger.info("Completed PCI bus driver init")
        # logger.info("Starting PCI bus driver init")
        # await bus_driver.init()
        # logger.info("Completed PCI bus driver init")
        # logger.info("Checking BAR0 addresses")
        # devices = 4
        # address_step = 0x100000
        # base_address = 0x80000000
        # for device_index in range(devices):
        #     address = base_address + address_step * device_index
        #     value_written = 0xDEADBEEF
        #     await host.write_mmio(0x80000000, 4, value_written)
        #     logger.info(f"Write 0x{value_written:X} at 0x{address:x}")
        #     value_read = await host.read_mmio(0x80000000, 4)
        #     logger.info(f"Read 0x{value_read:X} from 0x{address:x}")
        # logger.info("Completed Checking BAR0 addresses")

        # logger.info("Checking OOB Lower addresses")
        # address = address_step + devices * base_address
        # value_read = await host.read_mmio(address, 4)
        # logger.info(f"Read 0x{value_read:X} from 0x{address:x}")


def main():
    # Set up logger
    log_file = "test.log"
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

    # Add Host
    switch_host = "0.0.0.0"
    switch_port = 8000
    host_config = CxlComplexHostConfig(
        host_name="CXLHost",
        root_bus=0,
        root_port_switch_type=ROOT_PORT_SWITCH_TYPE.PASS_THROUGH,
        root_ports=[
            RootPortClientConfig(port_index=0, switch_host=switch_host, switch_port=switch_port)
        ],
        memory_ranges=[],
        memory_controller=RootComplexMemoryControllerConfig(
            memory_size=0x10000, memory_filename="memory_dram.bin"
        ),
    )
    host = CxlComplexHost(host_config)
    apps.append(host)

    # Add PCI devices
    for port in range(1, 5):
        device = PciDevice(port, 0x1000)
        apps.append(device)

    test_runner = TestRunner(apps)
    asyncio.run(test_runner.run())


if __name__ == "__main__":
    main()
