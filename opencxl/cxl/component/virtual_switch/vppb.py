"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from abc import abstractmethod
from asyncio import create_task, gather, run
from enum import IntEnum
from typing import cast

from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager
from opencxl.util.logger import logger
from opencxl.util.unaligned_bit_structure import (
    UnalignedBitStructure,
    BitField,
    FIELD_ATTR,
)
from opencxl.cxl.device.port_device import CxlPortDevice
from opencxl.cxl.device.downstream_port_device import DownstreamPortDevice
from opencxl.cxl.device.upstream_port_device import UpstreamPortDevice

from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager


class CURRENT_PORT_CONFIGURATION_STATE(IntEnum):
    DISABLED = 0x00
    BIND_IN_PROGRESS = 0x01
    UNBIND_IN_PROGRESS = 0x02
    DSP = 0x03
    USP = 0x04
    RESERVED = 0x05
    INVALID_PORT_ID = 0x0F


class CONNECTED_DEVICE_MODE(IntEnum):
    NOT_CXL_OR_DISCONNECTED = 0x00
    RCD_MODE = 0x01
    CXL_68B_FLIT_AND_VH_MODE = 0x02
    STANDARD_256B_FLIT_MODE = 0x03
    CXL_LATENCY_OPTIMIZED_256B_FLIT_MODE = 0x04
    PBR_MODE = 0x05


class CONNECTED_DEVICE_TYPE(IntEnum):
    NO_DEVICE_DETECTED = 0x00
    PCIE_DEVICE = 0x01
    CXL_TYPE1_DEVICE = 0x02
    CXL_TYPE2_DEVICE = 0x03
    CXL_TYPE3_SLD = 0x04
    CXL_TYPE3_MLD = 0x05
    RESERVED = 0x06


class SupportedCxlModes(UnalignedBitStructure):
    _fields = [
        BitField("rcd_mode", 0, 0),
        BitField("cxl_68b_flit_and_vh_capable", 1, 1),
        BitField("256b_flit_and_cxl_capable", 2, 2),
        BitField("cxl_latency_optimized_256b_flit_capable", 3, 3),
        BitField("pbr_capable", 4, 4),
        BitField("reserved", 5, 7, FIELD_ATTR.RESERVED),
    ]


class Vppb(RunnableComponent):
    def __init__(self):
        self._cxl_mem_manager: CxlMemManager = None
        self._cxl_io_manager: CxlIoManager = None
        self._cxl_cache_manager: CxlCacheManager = None

        self._pci_bridge_component = None
        self._pci_registers = None
        self._cxl_component = None
        # Initailize with dummy cxlconnection
        self._upstream_connection = CxlConnection()
        self._downstream_connection = CxlConnection()

        super().__init__()

    def get_upstream_connection(self) -> CxlConnection:
        return self._upstream_connection

    def get_downstream_connection(self) -> CxlConnection:
        return self._downstream_connection

    def bind_to_physical_port(self, physical_port: CxlPortDevice):

        if physical_port.get_device_type() == CXL_COMPONENT_TYPE.DSP:
            physical_port = cast(DownstreamPortDevice, physical_port)
        else:
            physical_port = cast(UpstreamPortDevice, physical_port)

        self._cxl_mem_manager = physical_port._cxl_mem_manager
        self._cxl_io_manager = physical_port._cxl_io_manager
        self._cxl_cache_manager = physical_port._cxl_cache_manager
        self._pci_bridge_component = physical_port._pci_bridge_component
        self._pci_registers = physical_port._pci_registers
        self._cxl_component = physical_port._cxl_component
        self._upstream_connection = physical_port._vppb_upstream_connection
        self._downstream_connection = physical_port._vppb_downstream_connection

    async def unbind_from_physical_port(self):
        self._cxl_mem_manager = None
        self._cxl_io_manager = None
        self._cxl_cache_manager = None
        self._pci_bridge_component = None
        self._pci_registers = None
        self._cxl_component = None
        self._upstream_connection = CxlConnection()
        self._downstream_connection = CxlConnection()

    @abstractmethod
    def set_routing_table(self, routing_table: RoutingTable):
        """This must be implemented in the child class"""

    @abstractmethod
    def get_device_type(self) -> CXL_COMPONENT_TYPE:
        """This must be implemented in the child class"""
