#!/usr/bin/env python

from signal import *
import asyncio
import sys, os

from opencxl.cxl.component.cxl_component import PORT_TYPE, PortConfig
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.virtual_switch_manager import VirtualSwitchConfig, VirtualSwitchManager

sw_conn_manager = None
physical_port_manager = None
virtual_switch_manager = None

start_tasks = []


async def shutdown(signame=None):
    global sw_conn_manager
    global physical_port_manager
    global virtual_switch_manager
    global start_tasks
    try:
        stop_tasks = [
            asyncio.create_task(sw_conn_manager.stop(), name="sw_conn_manager"),
            asyncio.create_task(physical_port_manager.stop(), name="phys_port_manager"),
            asyncio.create_task(virtual_switch_manager.stop(), name="virtual_switch_manager"),
        ]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        await asyncio.gather(*start_tasks)
    except Exception as exc:
        print("[SWITCH]", exc.__traceback__)
    finally:
        os._exit(0)


async def main():
    # install signal handlers
    lp = asyncio.get_event_loop()
    lp.add_signal_handler(SIGINT, lambda signame="SIGINT": asyncio.create_task(shutdown(signame)))

    portno = int(sys.argv[1])
    dev_count = int(sys.argv[2])

    global sw_conn_manager
    global physical_port_manager
    global virtual_switch_manager
    global start_tasks

    port_configs = [PortConfig(PORT_TYPE.USP)]
    for _ in range(dev_count):
        port_configs.append(PortConfig(PORT_TYPE.DSP))

    switch_configs = [
        VirtualSwitchConfig(
            upstream_port_index=0,
            vppb_counts=dev_count,
            initial_bounds=list(range(1, dev_count + 1)),
            irq_host="0.0.0.0",
            irq_port=8500,
        )
    ]
    allocated_ld = {}
    for index in range(dev_count):
        allocated_ld[index + 1] = [0]

    sw_conn_manager = SwitchConnectionManager(port_configs, host="localhost", port=portno)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=sw_conn_manager, port_configs=port_configs
    )
    virtual_switch_manager = VirtualSwitchManager(
        switch_configs=switch_configs, physical_port_manager=physical_port_manager, allocated_ld=allocated_ld
    )

    start_tasks = [
        asyncio.create_task(sw_conn_manager.run()),
        asyncio.create_task(physical_port_manager.run()),
        asyncio.create_task(virtual_switch_manager.run()),
    ]
    ready_tasks = [
        asyncio.create_task(sw_conn_manager.wait_for_ready()),
        asyncio.create_task(physical_port_manager.wait_for_ready()),
        asyncio.create_task(virtual_switch_manager.wait_for_ready()),
    ]
    await asyncio.gather(*ready_tasks)

    os.kill(os.getppid(), SIGCONT)

    await asyncio.Event().wait()  # blocks


if __name__ == "__main__":
    asyncio.run(main())
