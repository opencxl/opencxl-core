"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import StreamReader, create_task
from opencxl.cxl.transport.transaction import CciMessageHeaderPacket, CciMessagePacket
from opencxl.util.component import LabeledComponent
from typing import Optional
from opencxl.util.logger import logger


class MctpPacketReader(LabeledComponent):
    def __init__(self, reader: StreamReader, label: Optional[str] = None):
        super().__init__(label)
        self._reader = reader
        self._aborted = False
        self._task = None

    async def get_packet(self) -> CciMessagePacket:
        if self._aborted:
            raise Exception("PacketReader is already aborted")
        try:
            self._task = create_task(self._get_packet_in_task())
            packet = await self._task
        except:
            logger.debug(self._create_message("Aborted"))
            raise Exception("PacketReader is aborted")
        finally:
            self._task = None
        return packet

    def abort(self):
        if self._aborted:
            return
        logger.debug(self._create_message("Aborting"))
        self._aborted = True
        if self._task != None:
            self._task.cancel()

    async def _get_packet_in_task(self) -> CciMessagePacket:
        message_header = await self._get_cci_message_header()
        payload_length = message_header.get_message_payload_length()
        if payload_length > 0:
            payload_data = await self._read_payload(payload_length)
        else:
            payload_data = bytes()
        packet = CciMessagePacket.create(header=message_header, data=payload_data)
        return packet

    async def _get_cci_message_header(self) -> CciMessageHeaderPacket:
        logger.debug(self._create_message("Waiting for CCI Message Header"))
        payload = await self._read_payload(CciMessageHeaderPacket.get_size())
        message_header = CciMessageHeaderPacket()
        message_header.reset(payload)
        logger.debug(self._create_message("Received CCI Message Header"))
        return message_header

    async def _read_payload(self, size: int) -> bytes:
        payload = await self._reader.read(size)
        if not payload:
            raise Exception("Connection disconnected")
        return payload
