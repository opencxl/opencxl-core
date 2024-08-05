#!/usr/bin/env python

from signal import *
import asyncio
import sys, os
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
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver

host = None

start_tasks = []

train_data_path = None


async def shutdown(signame=None):
    global host
    global start_tasks
    try:
        stop_tasks = [
            asyncio.create_task(host.stop()),
        ]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        await asyncio.gather(*start_tasks)
    except Exception as exc:
        print("[HOST]", exc.__traceback__)
    finally:
        os._exit(0)


async def run_demo(signame=None):
    # the other devices are passively running, but the host
    # is responsible for executing the actual demo.

    global host

    print("[HOST] IO ready, running test")

    pci_bus_driver = PciBusDriver(host.get_root_complex())
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())

    await pci_bus_driver.init()
    await cxl_bus_driver.init()
    await cxl_mem_driver.init()

    hpa_base = 0x0
    next_available_hpa_base = hpa_base

    # hack for demo purposes
    for device in cxl_mem_driver.get_devices():
        size = device.get_memory_size()
        successful = await cxl_mem_driver.attach_single_mem_device(
            device, next_available_hpa_base, size
        )
        if successful:
            print("[HOST] device attached")
            host.append_dev_mmio_range(
                device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
            )
            host.append_dev_mem_range(next_available_hpa_base, size)
            next_available_hpa_base += size

    print("[HOST] all devices enumerated")

    await host.start_job()

    print(f"[HOST] demo done!")
    os.kill(os.getppid(), SIGINT)


async def main():
    # install signal handlers
    lp = asyncio.get_event_loop()
    lp.add_signal_handler(SIGINT, lambda signame="SIGINT": asyncio.create_task(shutdown(signame)))
    lp.add_signal_handler(SIGIO, lambda signame="SIGIO": asyncio.create_task(run_demo(signame)))

    sw_portno = int(sys.argv[1])
    global train_data_path
    train_data_path = sys.argv[2] if len(sys.argv) > 2 else None

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
        train_data_path,
        memory_controller,
        memory_ranges,
        root_ports,
        coh_type=COH_POLICY_TYPE.DotMemBI,
        device_type=CXL_COMPONENT_TYPE.T2,
    )
    host = CxlImageClassificationHost(config)

    start_tasks = [
        asyncio.create_task(host.run()),
    ]
    ready_tasks = [
        asyncio.create_task(host.wait_for_ready()),
    ]

    os.kill(os.getppid(), SIGCONT)

    await asyncio.gather(*ready_tasks)
    print("[HOST] ready!")

    await asyncio.Event().wait()  # blocks


if __name__ == "__main__":
    asyncio.run(main())
