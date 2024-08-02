#!/Users/ben/Library/Caches/pypoetry/virtualenvs/opencxl-5A3JHOT6-py3.11/bin/python3.11

from signal import *
import asyncio
import sys, os
from opencxl.apps.cxl_complex_host import CxlComplexHost, CxlComplexHostConfig

from opencxl.cxl.component.root_complex.home_agent import MEMORY_RANGE_TYPE, MemoryRange
from opencxl.cxl.component.root_complex.root_complex import RootComplexMemoryControllerConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.cxl.component.root_complex.root_port_switch import (
    COH_POLICY_TYPE,
    ROOT_PORT_SWITCH_TYPE,
)

host = None

start_tasks = []

async def shutdown(signame=None):
    global host
    global start_tasks
    try:
        stop_tasks = [
            asyncio.create_task(host.stop()),
        ]
    except Exception as exc:
        print("[HOST]", exc.__traceback__)
        quit()
    await asyncio.gather(*stop_tasks, return_exceptions=True)
    await asyncio.gather(*start_tasks)
    os._exit(0)


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

    config = CxlComplexHostConfig(
        host_name,
        0,
        root_port_switch_type,
        memory_controller,
        memory_ranges,
        root_ports,
        coh_type=COH_POLICY_TYPE.DotMemBI,
    )

    host = CxlComplexHost(config)

    start_tasks = [
        asyncio.create_task(host.run()),
    ]
    ready_tasks = [
        asyncio.create_task(host.wait_for_ready()),
    ]

    os.kill(os.getppid(), SIGCONT)

    await asyncio.gather(*ready_tasks)
    print("[HOST] ready!")

    await asyncio.Event().wait() # blocks


if __name__ == "__main__":
    asyncio.run(main())
