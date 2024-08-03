#!/usr/bin/env python

from signal import *
import asyncio
import sys, os
from opencxl.apps.accelerator import MyType2Accelerator

from opencxl.cxl.component.cxl_component import PORT_TYPE, PortConfig
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.virtual_switch_manager import VirtualSwitchConfig, VirtualSwitchManager
from opencxl.util.number_const import MB

device = None

start_tasks = []

async def shutdown(signame=None):
    global device
    global start_tasks
    try:
        stop_tasks = [
            asyncio.create_task(device.stop()),
        ]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        await asyncio.gather(*start_tasks)
    except Exception as exc:
        print("[HOST]", exc.__traceback__)
    finally:
        os._exit(0)

async def main():
    # install signal handlers
    lp = asyncio.get_event_loop()
    lp.add_signal_handler(SIGINT, lambda signame="SIGINT": asyncio.create_task(shutdown(signame)))

    sw_portno = int(sys.argv[1])
    portidx = int(sys.argv[2])

    print(f"[ACCEL] listening on port {sw_portno} and physical port {portidx}")

    global device
    global start_tasks

    device = MyType2Accelerator(
        port_index=portidx,
        memory_size=256 * MB,  # min 256MB, or will cause error for DVSEC
        memory_file=f"mem{sw_portno}.bin",
        host="localhost",
        port=sw_portno,
    )

    start_tasks = [
        asyncio.create_task(device.run()),
    ]
    ready_tasks = [
        asyncio.create_task(device.wait_for_ready()),
    ]

    os.kill(os.getppid(), SIGCONT)

    await asyncio.gather(*ready_tasks)
    print("[ACCEL] ready!")
    
    await asyncio.Event().wait() # blocks

if __name__ == "__main__":
    asyncio.run(main())
