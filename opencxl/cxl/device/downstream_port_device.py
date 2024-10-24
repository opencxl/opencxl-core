"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional

from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.component.cxl_bridge_component import (
    CxlDownstreamPortComponent,
)
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
from opencxl.cxl.device.port_device import CxlPortDevice
from opencxl.cxl.device.pci_to_pci_bridge_device import PpbDevice
from opencxl.cxl.config_space.port import (
    CxlDownstreamPortConfigSpace,
    CxlDownstreamPortConfigSpaceOptions,
)
from opencxl.cxl.config_space.dvsec import (
    DvsecConfigSpaceOptions,
    DvsecRegisterLocatorOptions,
    CXL_DEVICE_TYPE,
)
from opencxl.pci.component.pci import (
    PciComponentIdentity,
    PciBridgeComponent,
    PCI_BRIDGE_TYPE,
    PCI_CLASS,
    PCI_BRIDGE_SUBCLASS,
    PCI_DEVICE_PORT_TYPE,
    EEUM_VID,
    SW_DSP_DID,
)
from opencxl.pci.component.mmio_manager import MmioManager, BarEntry
from opencxl.pci.component.config_space_manager import (
    ConfigSpaceManager,
    PCI_DEVICE_TYPE,
)


class DownstreamPortDevice(CxlPortDevice):
    def __init__(
        self,
        transport_connection: CxlConnection,
        port_index: int = 0,
    ):
        super().__init__(transport_connection, port_index)

        self._pci_bridge_component = None
        self._pci_registers = None
        self._cxl_component = None

        self._ppb_device: PpbDevice = None
        self._ppb_bind = None
        self._vppb_index = -1

        self._cxl_io_manager = CxlIoManager(
            self._vppb_upstream_connection.mmio_fifo,
            self._vppb_downstream_connection.mmio_fifo,
            self._vppb_upstream_connection.cfg_fifo,
            self._vppb_downstream_connection.cfg_fifo,
            device_type=PCI_DEVICE_TYPE.DOWNSTREAM_BRIDGE,
            init_callback=self._init_device,
            label=self._get_label(),
        )
        self._cxl_mem_manager = CxlMemManager(
            upstream_fifo=self._vppb_upstream_connection.cxl_mem_fifo,
            downstream_fifo=self._vppb_downstream_connection.cxl_mem_fifo,
            label=self._get_label(),
        )
        self._cxl_cache_manager = CxlCacheManager(
            upstream_fifo=self._vppb_upstream_connection.cxl_cache_fifo,
            downstream_fifo=self._vppb_downstream_connection.cxl_cache_fifo,
            label=self._get_label(),
        )

    def _init_device(
        self,
        mmio_manager: MmioManager,
        config_space_manager: ConfigSpaceManager,
    ):
        pci_identity = PciComponentIdentity(
            vendor_id=EEUM_VID,
            device_id=SW_DSP_DID,
            base_class_code=PCI_CLASS.BRIDGE,
            sub_class_coce=PCI_BRIDGE_SUBCLASS.PCI_BRIDGE,
            programming_interface=0x00,
            device_port_type=PCI_DEVICE_PORT_TYPE.DOWNSTREAM_PORT_OF_PCI_EXPRESS_SWITCH,
        )
        self._pci_bridge_component = PciBridgeComponent(
            identity=pci_identity,
            type=PCI_BRIDGE_TYPE.DOWNSTREAM_PORT,
            mmio_manager=mmio_manager,
            label=self._get_label(),
        )

        # Create MMIO register
        cxl_component = CxlDownstreamPortComponent()
        self._cxl_component = cxl_component
        mmio_options = CombinedMmioRegiterOptions(cxl_component=cxl_component)
        mmio_register = CombinedMmioRegister(options=mmio_options)
        mmio_manager.set_bar_entries([BarEntry(mmio_register)])

        # Create Config Space Register
        pci_registers_options = CxlDownstreamPortConfigSpaceOptions(
            pci_bridge_component=self._pci_bridge_component,
            dvsec=DvsecConfigSpaceOptions(
                device_type=CXL_DEVICE_TYPE.DSP,
                register_locator=DvsecRegisterLocatorOptions(
                    registers=mmio_register.get_dvsec_register_offsets()
                ),
            ),
        )
        self._pci_registers = CxlDownstreamPortConfigSpace(options=pci_registers_options)
        config_space_manager.set_register(self._pci_registers)

    def _get_label(self) -> str:
        return f"DSP{self._port_index}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    def set_vppb_index(self, vppb_index: int):
        self._vppb_index = vppb_index
        self._pci_bridge_component.set_port_number(self._vppb_index)

    def get_device_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.DSP

    def set_routing_table(self, routing_table: RoutingTable):
        self._pci_bridge_component.set_routing_table(routing_table)

    def get_secondary_bus_number(self):
        return self._pci_registers.pci.secondary_bus_number

    def get_cxl_component(self) -> CxlDownstreamPortComponent:
        return self._cxl_component

    def set_ppb(self, ppb_device: PpbDevice, ppb_bind):
        self._ppb_device = ppb_device
        self._ppb_bind = ppb_bind

    def get_ppb_device(self) -> Optional[PpbDevice]:
        return self._ppb_device

    def get_ppb_bind(self):
        return self._ppb_bind
