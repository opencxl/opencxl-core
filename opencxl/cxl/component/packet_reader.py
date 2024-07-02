"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import CancelledError, StreamReader, create_task
from enum import Enum, auto
import traceback
from typing import Optional, Tuple
from ctypes import sizeof
from opencxl.cxl.transport.common import BasePacket, SystemHeader
from opencxl.cxl.transport.transaction import (
    BaseSidebandPacket,
    SidebandConnectionRequestPacket,
    CxlIoBasePacket,
    CxlIoCfgRdPacket,
    CxlIoCfgWrPacket,
    CxlIoMemRdPacket,
    CxlIoMemWrPacket,
    CxlIoMemWr32Packet,
    CxlIoMemWr64Packet,
    CxlIoCplPacket,
    CxlIoCplData32Packet,
    CxlIoCplData64Packet,
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
    def __init__(
        self, reader: StreamReader, label: Optional[str] = None, parent_name: Optional[str] = None
    ):
        label_prefix = f"{parent_name}:" if parent_name else ""
        label_suffix = f":{label}" if label else ""
        super().__init__(lambda class_name: f"{label_prefix}{class_name}{label_suffix}")
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
        base_packet, payload = await self._get_payload()
        if base_packet.is_cxl_io():
            logger.debug(self._create_message("Received Packet is CXL.io"))
            return self._get_cxl_io_packet(payload)
        if base_packet.is_cxl_mem():
            logger.debug(self._create_message("Received Packet is CXL.mem"))
            return self._get_cxl_mem_packet(payload)
        if base_packet.is_sideband():
            logger.debug(self._create_message("Received Packet is sideband"))
            return self._get_sideband_packet(payload)
        raise Exception("Unsupported packet")

    async def _get_payload(self) -> Tuple[BasePacket, bytes]:
        logger.debug(self._create_message("Waiting Packet"))
        header_payload = await self._read_payload(sizeof(SystemHeader))
        base_packet = BasePacket.from_buffer_copy(header_payload)
        remaining_length = base_packet.system_header.payload_length - sizeof(SystemHeader)
        if remaining_length < 0:
            raise Exception("remaining length is less than 0")
        payload = header_payload + await self._read_payload(remaining_length)
        logger.debug(self._create_message("Received Packet"))
        return base_packet, payload

    async def _read_payload(self, size: int) -> bytes:
        payload = await self._reader.read(size)
        if not payload:
            raise Exception("Connection disconnected")
        return payload

    def _get_cxl_io_packet(self, payload: bytes) -> CxlIoBasePacket:
        cxl_io_base_packet = CxlIoBasePacket.from_buffer_copy(payload)
        if cxl_io_base_packet.is_cfg_read():
            packet_type = CxlIoCfgRdPacket
        elif cxl_io_base_packet.is_cfg_write():
            packet_type = CxlIoCfgWrPacket
        elif cxl_io_base_packet.is_mem_read():
            packet_type = CxlIoMemRdPacket
        elif cxl_io_base_packet.is_mem_write():
            packet_type = CxlIoMemWrPacket
            data_size = len(payload) - sizeof(CxlIoMemWrPacket)
            if data_size == 4:
                packet_type = CxlIoMemWr32Packet
            else:
                packet_type = CxlIoMemWr64Packet
        elif cxl_io_base_packet.is_cpl():
            packet_type = CxlIoCplPacket
        elif cxl_io_base_packet.is_cpld():
            data_size = len(payload) - sizeof(CxlIoCplPacket)
            if data_size == 4:
                packet_type = CxlIoCplData32Packet
            else:
                packet_type = CxlIoCplData64Packet

        if packet_type is None:
            protocol = cxl_io_base_packet.cxl_io_header.fmt_type
            raise Exception(f"Unsupported CXL.IO protocol {protocol}")

        cxl_io_packet = packet_type.from_buffer_copy(payload)
        return cxl_io_packet

    def _get_cxl_mem_packet(self, payload: bytes):
        cxl_mem_base_packet = CxlMemBasePacket.from_buffer_copy(payload)
        if cxl_mem_base_packet.is_m2sreq():
            cxl_mem_packet = CxlMemM2SReqPacket
        elif cxl_mem_base_packet.is_m2srwd():
            cxl_mem_packet = CxlMemM2SRwDPacket
        elif cxl_mem_base_packet.is_m2sbirsp():
            cxl_mem_packet = CxlMemM2SBIRspPacket
        elif cxl_mem_base_packet.is_s2mbisnp():
            cxl_mem_packet = CxlMemS2MBISnpPacket
        elif cxl_mem_base_packet.is_s2mndr():
            cxl_mem_packet = CxlMemS2MNDRPacket
        elif cxl_mem_base_packet.is_s2mdrs():
            cxl_mem_packet = CxlMemS2MDRSPacket
        else:
            msg_class = cxl_mem_base_packet.cxl_mem_header.msg_class
            raise Exception(f"Unsupported CXL.MEM message class: {msg_class}")

        cxl_mem_packet = cxl_mem_packet.from_buffer_copy(payload)
        return cxl_mem_packet

    def _get_sideband_packet(self, payload: bytes) -> BaseSidebandPacket:
        base_sideband_packet = BaseSidebandPacket.from_buffer_copy(payload)
        if base_sideband_packet.is_connection_request():
            sideband_packet = SidebandConnectionRequestPacket.from_buffer_copy(payload)
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
