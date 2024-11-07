"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import socketio
import asyncio
from aiohttp import web
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.mctp.mctp_cci_api_client import (
    MctpCciApiClient,
    GetPhysicalPortStateRequestPayload,
    GetVirtualCxlSwitchInfoRequestPayload,
    IdentifySwitchDeviceResponsePayload,
    BindVppbRequestPayload,
    UnbindVppbRequestPayload,
    CciMessagePacket,
)
from opencxl.cxl.cci.common import (
    CCI_VENDOR_SPECIFIC_OPCODE,
    get_opcode_string,
)
from typing import TypedDict, Optional, Any
from pprint import pformat
from opencxl.util.logger import logger
from functools import partial


class CommandResponse(TypedDict):
    error: str
    result: Any


class FabricManagerSocketIoServer(RunnableComponent):
    def __init__(self, mctp_client: MctpCciApiClient, host: str = "0.0.0.0", port: int = 8200):
        super().__init__()
        self._mctp_client = mctp_client
        self._host = host
        self._port = port
        self._event_lock = asyncio.Lock()
        self._switch_identity = None
        self._stop_signal = False

        # Create a new Aiohttp web app
        self._app = web.Application()

        # Create a Socket.IO server
        self._sio = socketio.AsyncServer(cors_allowed_origins="*")
        self._sio.attach(self._app)
        self._runner = web.AppRunner(self._app)

        self._register_handler("port:get")
        self._register_handler("vcs:get")
        self._register_handler("device:get")
        self._register_handler("vcs:bind")
        self._register_handler("vcs:unbind")
        self._mctp_client.register_notification_handler(self._handle_notifications)

    def _register_handler(self, event):
        self._sio.on(event, partial(self._handle_event, event))

    async def _handle_notifications(self, packet: CciMessagePacket):
        logger.debug(self._create_message("Handling Notification"))
        opcode = packet.header.command_opcode
        opcode_str = get_opcode_string(opcode)
        if opcode == CCI_VENDOR_SPECIFIC_OPCODE.NOTIFY_PORT_UPDATE:
            await self._send_update_physical_ports_notification()
        elif opcode == CCI_VENDOR_SPECIFIC_OPCODE.NOTIFY_SWITCH_UPDATE:
            await self._send_update_virtual_cxl_switches_notification()
        elif opcode == CCI_VENDOR_SPECIFIC_OPCODE.NOTIFY_DEVICE_UPDATE:
            await self._send_update_devices_notification()
        else:
            logger.debug(self._create_message(f"Unexpected Packet {opcode_str}"))

    async def _handle_event(self, event_type, _, data=None):
        async with self._event_lock:
            # Determine the event type and call the appropriate method
            logger.debug(self._create_message(f"Received SocketIO Request: {event_type}"))
            if event_type == "port:get":
                response = await self._get_physical_ports()
            elif event_type == "vcs:get":
                response = await self._get_virtual_switches()
            elif event_type == "device:get":
                response = await self._get_devices()
            elif event_type == "vcs:bind":
                response = await self._bind_vppb(data)
            elif event_type == "vcs:unbind":
                response = await self._unbind_vppb(data)
            # logger.debug(self._create_message(f"Response: {pformat(response)}"))
            logger.debug(self._create_message(f"Completed SocketIO Request"))
            return response

    async def _get_switch_identity(self) -> IdentifySwitchDeviceResponsePayload:
        if self._switch_identity == None:
            (_, response) = await self._mctp_client.identify_switch_device()
            if not response:
                raise Exception("Failed to get switch identity")
            self._switch_identity = response
        return self._switch_identity

    async def _get_physical_ports(self) -> CommandResponse:
        switch_identity = await self._get_switch_identity()
        port_id_list = list(range(switch_identity.num_physical_ports))
        request = GetPhysicalPortStateRequestPayload(port_id_list)
        (return_code, response) = await self._mctp_client.get_physical_port_state(request)
        if response:
            return CommandResponse(error="", result=response.to_dict()["portInfoList"])
        else:
            return CommandResponse(error=return_code.name)

    async def _get_virtual_switches(self) -> CommandResponse:
        switch_identity = await self._get_switch_identity()
        vcs_id_list = list(range(switch_identity.num_vcss))
        request = GetVirtualCxlSwitchInfoRequestPayload(
            start_vppb=0, vppb_list_limit=255, vcs_id_list=vcs_id_list
        )
        (return_code, response) = await self._mctp_client.get_virtual_cxl_switch_info(request)
        if response:
            return CommandResponse(error="", result=response.to_dict()["vcsInfoList"])
        else:
            return CommandResponse(error=return_code.name)

    async def _get_devices(self) -> CommandResponse:
        (return_code, response) = await self._mctp_client.get_connected_devices()
        if response:
            return CommandResponse(error="", result=response.to_dict()["devices"])
        else:
            return CommandResponse(error=return_code.name)

    async def _bind_vppb(self, data) -> CommandResponse:
        request = BindVppbRequestPayload(
            vcs_id=data["virtualCxlSwitchId"],
            vppb_id=data["vppbId"],
            physical_port_id=data["physicalPortId"],
            ld_id=data["ldId"],
        )
        (return_code, response) = await self._mctp_client.bind_vppb(request)
        if response:
            return CommandResponse(error="", result=response.name)
        else:
            return CommandResponse(error="", result=return_code.name)

    async def _unbind_vppb(self, data) -> CommandResponse:
        request = UnbindVppbRequestPayload(
            vcs_id=data["virtualCxlSwitchId"],
            vppb_id=data["vppbId"],
        )
        (return_code, response) = await self._mctp_client.unbind_vppb(request)
        if response:
            return CommandResponse(error="", result=response.name)
        else:
            return CommandResponse(error="", result=return_code.name)

    async def _send_update_physical_ports_notification(self):
        # Emitting event without arguments
        await self._sio.emit("port:updated")

    async def _send_update_virtual_cxl_switches_notification(self):
        # Emitting event without arguments
        await self._sio.emit("vcs:updated")

    async def _send_update_devices_notification(self):
        # Emitting event without arguments
        await self._sio.emit("device:updated")

    async def _run(self):
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.debug(
            self._create_message(f"Creating SocketIO Server at http://{self._host}:{self._port}")
        )
        await self._change_status_to_running()

        # Here you could add a condition to run indefinitely or until a stop signal is received
        while not self._stop_signal:
            await asyncio.sleep(1)  # Run forever or until a stop signal is set

    async def _stop(self):
        self._stop_signal = True
        await self._runner.cleanup()
