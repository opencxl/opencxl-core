"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from abc import abstractmethod
from typing import cast

from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager
from opencxl.cxl.device.port_device import CxlPortDevice
from opencxl.cxl.device.downstream_port_device import DownstreamPortDevice
from opencxl.cxl.device.upstream_port_device import UpstreamPortDevice

from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager


class Vppb:
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

        # pylint: disable=protected-access
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
