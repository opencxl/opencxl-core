"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from typing import Optional, cast, List
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.mctp.mctp_connection import MctpConnection
from opencxl.cxl.component.cci_executor import (
    CciExecutor,
    CciRequest,
    CciResponse,
    CciCommand,
    CciBackgroundStatus,
)
from opencxl.cxl.transport.transaction import (
    CciMessagePacket,
    CciMessageHeaderPacket,
    CCI_MCTP_MESSAGE_CATEGORY,
)
from opencxl.cxl.cci.common import get_opcode_string
from opencxl.util.logger import logger


class MctpCciExecutor(RunnableComponent):
    def __init__(self, mctp_connection: MctpConnection, label: Optional[str] = None):
        super().__init__(label)
        self._mctp_connection = mctp_connection
        self._cci_executor = CciExecutor(label="MCTP")

    def register_cci_commands(self, commands: List[CciCommand]):
        for command in commands:
            self._cci_executor.register_command(command.get_opcode(), command)

    def _packet_to_request(self, packet: CciMessagePacket) -> CciRequest:
        return CciRequest(opcode=packet.header.command_opcode, payload=packet.get_payload())

    async def _send_response(self, response: CciResponse, message_tag: int):
        header = CciMessageHeaderPacket()
        header.message_category = CCI_MCTP_MESSAGE_CATEGORY.RESPONSE
        header.message_tag = message_tag
        header.command_opcode = 0
        header.set_message_payload_length(len(response.payload))
        header.background_operation = 1 if response.bo_flag else 0
        header.return_code = response.return_code
        header.vendor_specific_extended_status = response.vendor_specific_status
        response_packet = CciMessagePacket.create(header, response.payload)
        await self._mctp_connection.ep_to_controller.put(response_packet)

    async def _process_incoming_requests(self):
        logger.debug(self._create_message("Started processing incoming request"))
        while True:
            # Wait for incoming packets from the MCTP connection
            packet = await self._mctp_connection.controller_to_ep.get()
            if packet == None:
                logger.debug(self._create_message("Stopped processing incoming request"))
                break

            cci_packet = cast(CciMessagePacket, packet)

            # Convert packet to CciRequest and send it to CciExecutor
            request = self._packet_to_request(cci_packet)
            response = await self._cci_executor.execute_command(request)
            await self._send_response(response, packet.header.message_tag)

    async def _run(self):
        tasks = [
            create_task(self._process_incoming_requests()),
            create_task(self._cci_executor.run()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        # Stop the executor
        await self._mctp_connection.controller_to_ep.put(None)
        await self._cci_executor.stop()

    async def get_background_command_status(self) -> CciBackgroundStatus:
        status = await self._cci_executor.get_background_command_status()
        return status

    async def send_notification(self, request: CciRequest):
        header = CciMessageHeaderPacket()
        header.message_category = CCI_MCTP_MESSAGE_CATEGORY.REQUEST
        header.set_message_payload_length(len(request.payload))
        header.command_opcode = request.opcode
        message_packet = CciMessagePacket.create(header, request.payload)
        opcode_str = get_opcode_string(request.opcode)
        logger.debug(self._create_message(f"Sending {opcode_str}"))
        await self._mctp_connection.ep_to_controller.put(message_packet)
