"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""
import socketio
import asyncio
import sys
from yaml import dump

# Standard Python client setup for Socket.IO
sio = socketio.AsyncClient()


@sio.on("port:updated")
def handle_port_updated():
    print("[Notification]")
    print("port:updated")


@sio.on("vcs:updated")
def handle_port_updated():
    print("[Notification]")
    print("port:updated")


@sio.on("device:updated")
def handle_port_updated():
    print("[Notification]")
    print("device:updated")


class CustomSemaphore(asyncio.Semaphore):
    def __init__(self, value=0, custom_value=None):
        super().__init__(value)
        self.custom_value = custom_value

    def set_custom_value(self, value):
        self.custom_value = value


async def send(event, param=None):
    sema = CustomSemaphore()

    def callback_handler(result):
        sema.set_custom_value(result)
        sema.release()

    print(f"[Request]")
    print(event)
    await sio.emit(event, param, callback=callback_handler)
    await sema.acquire()
    result = sema.custom_value

    print(f"[Response]")
    print_result(result)
    return result


def print_result(data):
    print(dump(data, sort_keys=False, default_flow_style=False))


# Connect event handler
@sio.event
async def connect():
    print("Connected to the server")

    await send("port:get")

    await send("vcs:get")

    await send("device:get")

    await send("vcs:unbind", {"virtualCxlSwitchId": 0, "vppbId": 0})
    await send("vcs:unbind", {"virtualCxlSwitchId": 0, "vppbId": 1})
    await send("vcs:unbind", {"virtualCxlSwitchId": 0, "vppbId": 2})
    await send("vcs:unbind", {"virtualCxlSwitchId": 0, "vppbId": 3})

    await send(
        "vcs:bind",
        {"virtualCxlSwitchId": 0, "vppbId": 0, "physicalPortId": 1},
    )
    await send(
        "vcs:bind",
        {"virtualCxlSwitchId": 0, "vppbId": 1, "physicalPortId": 2},
    )
    await send(
        "vcs:bind",
        {"virtualCxlSwitchId": 0, "vppbId": 2, "physicalPortId": 3},
    )
    await send(
        "vcs:bind",
        {"virtualCxlSwitchId": 0, "vppbId": 3, "physicalPortId": 4},
    )
    # await stop_client()


# Disconnect event handler
@sio.event
def disconnect():
    print("Disconnected from server")
    sys.exit()


# Main asynchronous function to start the client
async def start_client():
    await sio.connect("http://0.0.0.0:8200")
    await sio.wait()


# Stop the client gracefully
async def stop_client():
    await sio.disconnect()


# Run the client
if __name__ == "__main__":
    asyncio.run(start_client())
