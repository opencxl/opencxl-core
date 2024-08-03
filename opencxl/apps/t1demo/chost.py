#!/usr/bin/env python3

from signal import *
import asyncio
import sys, os
from opencxl.apps.cxl_complex_host import CxlComplexHost, CxlComplexHostConfig

from opencxl.apps.cxl_image_classification_host import (
    CxlImageClassificationHost,
    CxlImageClassificationHostConfig,
)
from opencxl.cxl.component.root_complex.home_agent import MEMORY_RANGE_TYPE, MemoryRange
from opencxl.cxl.component.root_complex.root_complex import RootComplexMemoryControllerConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import (
    COH_POLICY_TYPE,
    ROOT_PORT_SWITCH_TYPE,
)
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver

host: CxlImageClassificationHost = None

start_tasks = []
stop_signal = asyncio.Event()


async def shutdown(signame=None):
    global host
    global start_tasks
    global stop_signal
    try:
        stop_tasks = [
            asyncio.create_task(host.stop()),
        ]
        stop_signal.set()
    except Exception as exc:
        print("[HOST]", exc.__traceback__)
        quit()
    await asyncio.gather(*stop_tasks, return_exceptions=True)
    await asyncio.gather(*start_tasks)
    print("Host quitted")
    os._exit(0)


async def start_host():
    global host
    global stop_signal
    print("Starting job")
    await host.start_job()
    print("Job ends")
    stop_signal.set()
    print("Stop signal set")


async def main():
    # install signal handlers
    lp = asyncio.get_event_loop()
    lp.add_signal_handler(SIGINT, lambda signame="SIGINT": asyncio.create_task(shutdown(signame)))

    sw_portno = int(sys.argv[1])
    print(f"[HOST] listening on port {sw_portno}")

    global host
    global start_tasks
    host_mem_size = 0x8000  # Needs to be big enough to test cache eviction

    host_name = "foo"
    root_port_switch_type = ROOT_PORT_SWITCH_TYPE.PASS_THROUGH
    memory_controller = RootComplexMemoryControllerConfig(host_mem_size, "foo.bin")
    root_ports = [RootPortClientConfig(0, "localhost", sw_portno)]
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

    host = CxlImageClassificationHost(config)

    pci_bus_driver = PciBusDriver(host.get_root_complex())
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())

    start_tasks = [
        asyncio.create_task(host.run()),
    ]
    ready_tasks = [
        asyncio.create_task(host.wait_for_ready()),
    ]

    os.kill(os.getppid(), SIGCONT)

    await asyncio.gather(*ready_tasks)

    await pci_bus_driver.init()
    await cxl_bus_driver.init()
    await cxl_mem_driver.init()

    cache_dev_count = 0
    for device in cxl_bus_driver.get_devices():
        if device.device_dvsec:
            if device.device_dvsec.cache_capable:
                cache_dev_count += 1
    print(f"cache_dev_count: {cache_dev_count}")

    host.set_device_count(cache_dev_count)
    host.get_root_complex().set_cache_coh_dev_count(cache_dev_count)

    for device in cxl_mem_driver.get_devices():
        # NOTE: The list should match the dev order
        # Not tested, though
        # otherwise the dev base may not match the IRQ ports
        host.append_dev_mmio_range(
            device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
        )
    asyncio.create_task(start_host())
    print("[HOST] ready!")

    await stop_signal.wait()

    os.kill(os.getppid(), SIGINT)


if __name__ == "__main__":
    asyncio.run(main())
