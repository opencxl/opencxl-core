"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import CancelledError, StreamReader, create_task
from enum import Enum, auto
import traceback
from typing import Optional

from opencxl.cxl.transport.transaction import (
    UnalignedBitStructure,
    BasePacket,
    BaseSidebandPacket,
    SidebandConnectionRequestPacket,
    CxlIoBasePacket,
    CxlIoCfgRdPacket,
    CxlIoCfgWrPacket,
    CxlIoMemRdPacket,
    CxlIoMemWrPacket,
    CxlIoCompletionPacket,
    CxlIoCompletionWithDataPacket,
    CxlMemBasePacket,
    CxlMemM2SReqPacket,
    CxlMemM2SRwDPacket,
    CxlMemM2SBIRspPacket,
    CxlMemS2MBISnpPacket,
    CxlMemS2MNDRPacket,
    CxlMemS2MDRSPacket,
)
from opencxl.util.logger import logger
from opencxl.util.component import LabeledComponent


class PACKET_READ_STATUS(Enum):
    OK = auto()
    DISCONNECTED = auto()
    TIMED_OUT = auto()


class PacketReader(LabeledComponent):
    def __init__(self, reader: StreamReader, label: Optional[str] = None):
        super().__init__(label)
        self._reader = reader
        self._aborted = False
        self._task = None

    async def get_packet(self) -> BasePacket:
        if self._aborted:
            raise Exception("PacketReader is already aborted")
        try:
            self._task = create_task(self._get_packet_in_task())
            packet = await self._task
        except Exception as e:
            logger.debug(self._create_message(str(e)))
            if str(e) != "Connection disconnected":
                logger.debug(traceback.format_exc())
            raise Exception("PacketReader is aborted") from e
        except CancelledError as exc:
            logger.debug(self._create_message("Aborted"))
            raise Exception("PacketReader is aborted") from exc
        finally:
            self._task = None
        return packet

    def abort(self):
        if self._aborted:
            return
        logger.debug(self._create_message("Aborting"))
        self._aborted = True
        if self._task is not None:
            self._task.cancel()

    async def _get_packet_in_task(self) -> BasePacket:
        base_packet = await self._get_base_packet()
        if base_packet.is_cxl_io():
            logger.debug(self._create_message("Received Packet is CXL.io"))
            return await self._get_cxl_io_packet(base_packet)
        if base_packet.is_cxl_mem():
            logger.debug(self._create_message("Received Packet is CXL.mem"))
            return await self._get_cxl_mem_packet(base_packet)
        if base_packet.is_sideband():
            logger.debug(self._create_message("Received Packet is sideband"))
            return await self._get_sideband_packet(base_packet)
        raise Exception("Unsupported packet")

    async def _get_base_packet(self) -> BasePacket:
        logger.debug(self._create_message("Waiting for Base Packet"))
        payload = await self._read_payload(BasePacket.get_size())
        base_packet = BasePacket()
        base_packet.reset(payload)
        logger.debug(self._create_message("Received Base Packet"))
        return base_packet

    async def _read_payload(self, size: int) -> bytes:
        payload = await self._reader.read(size)
        if not payload:
            raise Exception("Connection disconnected")
        return payload

    async def _extend_packet(
        self, base_packet: UnalignedBitStructure, new_packet: UnalignedBitStructure
    ):
        remaining_length = new_packet.get_size() - len(base_packet)
        if len(base_packet) == len(new_packet):
            new_packet.reset(bytes(base_packet))
            return new_packet
        if remaining_length < 0:
            raise Exception("remaining length is less than 0")

        payload = bytes(base_packet) + await self._read_payload(remaining_length)
        new_packet.reset(payload)
        return new_packet

    async def _get_cxl_io_base_packet(self, base_packet: BasePacket) -> CxlIoBasePacket:
        cxl_io_base_packet = CxlIoBasePacket()
        await self._extend_packet(base_packet, cxl_io_base_packet)
        return cxl_io_base_packet

    async def _get_cxl_io_packet(self, base_packet: BasePacket) -> CxlIoBasePacket:
        cxl_io_base_packet = await self._get_cxl_io_base_packet(base_packet)

        cxl_io_packet = None
        if cxl_io_base_packet.is_cfg_read():
            cxl_io_packet = CxlIoCfgRdPacket()
            await self._extend_packet(cxl_io_base_packet, cxl_io_packet)
        elif cxl_io_base_packet.is_cfg_write():
            cxl_io_packet = CxlIoCfgWrPacket()
            await self._extend_packet(cxl_io_base_packet, cxl_io_packet)
        elif cxl_io_base_packet.is_mem_read():
            cxl_io_packet = CxlIoMemRdPacket()
            await self._extend_packet(cxl_io_base_packet, cxl_io_packet)
        elif cxl_io_base_packet.is_mem_write():
            cxl_io_packet = CxlIoMemWrPacket()
            await self._extend_packet(cxl_io_base_packet, cxl_io_packet)
        elif cxl_io_base_packet.is_cpl():
            cxl_io_packet = CxlIoCompletionPacket()
            await self._extend_packet(cxl_io_base_packet, cxl_io_packet)
        elif cxl_io_base_packet.is_cpld():
            cxl_io_packet = CxlIoCompletionWithDataPacket()
            await self._extend_packet(cxl_io_base_packet, cxl_io_packet)

        if cxl_io_packet is None:
            protocol = cxl_io_base_packet.cxl_io_header.fmt_type
            raise Exception(f"Unsupported CXL.IO protocol {protocol}")

        return cxl_io_packet

    async def _get_cxl_mem_base_packet(self, base_packet: BasePacket) -> CxlMemBasePacket:
        cxl_mem_base_packet = CxlMemBasePacket()
        await self._extend_packet(base_packet, cxl_mem_base_packet)
        return cxl_mem_base_packet

    async def _get_cxl_mem_packet(self, base_packet: BasePacket) -> CxlMemBasePacket:
        cxl_mem_base_packet = await self._get_cxl_mem_base_packet(base_packet)

        cxl_mem_packet = None
        if cxl_mem_base_packet.is_m2sreq():
            cxl_mem_packet = CxlMemM2SReqPacket()
        elif cxl_mem_base_packet.is_m2srwd():
            cxl_mem_packet = CxlMemM2SRwDPacket()
        elif cxl_mem_base_packet.is_m2sbirsp():
            cxl_mem_packet = CxlMemM2SBIRspPacket()
        elif cxl_mem_base_packet.is_s2mbisnp():
            cxl_mem_packet = CxlMemS2MBISnpPacket()
        elif cxl_mem_base_packet.is_s2mndr():
            cxl_mem_packet = CxlMemS2MNDRPacket()
        elif cxl_mem_base_packet.is_s2mdrs():
            cxl_mem_packet = CxlMemS2MDRSPacket()
        else:
            msg_class = cxl_mem_base_packet.cxl_mem_header.msg_class
            raise Exception(f"Unsupported CXL.MEM message class: {msg_class}")

        await self._extend_packet(cxl_mem_base_packet, cxl_mem_packet)
        return cxl_mem_packet

    async def _get_sideband_base_packet(self, base_packet: BasePacket) -> BaseSidebandPacket:
        logger.debug(self._create_message("Waiting for remaining packets of BaseSidebandPacket"))
        base_sideband_packet = BaseSidebandPacket()
        await self._extend_packet(base_packet, base_sideband_packet)
        logger.debug(self._create_message("Received BaseSidebandPacket"))
        return base_sideband_packet

    async def _get_sideband_packet(self, base_packet: BasePacket) -> BaseSidebandPacket:
        base_sideband_packet = await self._get_sideband_base_packet(base_packet)
        sideband_packet = None
        if base_sideband_packet.is_connection_request():
            sideband_packet = SidebandConnectionRequestPacket()
            await self._extend_packet(base_sideband_packet, sideband_packet)
        elif (
            base_sideband_packet.is_connection_accept()
            or base_sideband_packet.is_connection_reject()
        ):
            sideband_packet = base_sideband_packet
        return sideband_packet

    async def _get_sideband_connection_request_packet(self, packets: bytes):
        remaining_packets_size = SidebandConnectionRequestPacket.get_size() - len(packets)
        remaining_packets = await self._reader.read(remaining_packets_size)
        if not remaining_packets:
            raise Exception("Connection disconnected")

        packets = packets + remaining_packets
        sideband = SidebandConnectionRequestPacket()
        sideband.reset(packets)

        return sideband
