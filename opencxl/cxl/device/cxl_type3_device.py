"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from enum import Enum, auto
from typing import Optional

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
from opencxl.cxl.config_space.dvsec import (
    CXL_DEVICE_TYPE,
    DvsecConfigSpaceOptions,
    DvsecRegisterLocatorOptions,
)
from opencxl.cxl.config_space.doe.doe import CxlDoeExtendedCapabilityOptions
from opencxl.cxl.config_space.device import (
    CxlType3SldConfigSpace,
    CxlType3SldConfigSpaceOptions,
)
from opencxl.cxl.component.cxl_memory_device_component import (
    CxlMemoryDeviceComponent,
    MemoryDeviceIdentity,
    HDM_DECODER_COUNT,
)
from opencxl.pci.component.pci import (
    PciComponent,
    PciComponentIdentity,
    EEUM_VID,
    SW_SLD_DID,
    PCI_CLASS,
    MEMORY_CONTROLLER_SUBCLASS,
)
from opencxl.pci.component.mmio_manager import MmioManager, BarEntry
from opencxl.pci.component.config_space_manager import (
    ConfigSpaceManager,
    PCI_DEVICE_TYPE,
)


class CXL_T3_DEV_TYPE(Enum):
    SLD = auto()
    MLD = auto()


class CxlType3Device(RunnableComponent):
    def __init__(
        self,
        transport_connection: CxlConnection,
        memory_size: int,
        memory_file: str,
        dev_type: CXL_T3_DEV_TYPE,
        decoder_count: HDM_DECODER_COUNT = HDM_DECODER_COUNT.DECODER_4,
        label: Optional[str] = None,
        ld_id: Optional[int] = None,
    ):
        # pylint: disable=unused-argument
        super().__init__(label)
        self._memory_size = memory_size
        self._memory_file = memory_file
        self._decoder_count = decoder_count
        self._cxl_memory_device_component = None
        self._upstream_connection = transport_connection
        self._ld_id = ld_id

        self._cxl_io_manager = CxlIoManager(
            self._upstream_connection.mmio_fifo,
            None,
            self._upstream_connection.cfg_fifo,
            None,
            device_type=PCI_DEVICE_TYPE.ENDPOINT,
            init_callback=self._init_device,
            label=self._label,
            ld_id=self._ld_id,
        )
        self._cxl_mem_manager = CxlMemManager(
            upstream_fifo=self._upstream_connection.cxl_mem_fifo,
            label=self._label,
            ld_id=self._ld_id,
        )

        # Update CxlMemManager with a CxlMemoryDeviceComponent
        self._cxl_mem_manager.set_memory_device_component(self._cxl_memory_device_component)

    def _init_device(
        self,
        mmio_manager: MmioManager,
        config_space_manager: ConfigSpaceManager,
    ):
        # Create PCiComponent
        pci_identity = PciComponentIdentity(
            vendor_id=EEUM_VID,
            device_id=SW_SLD_DID,
            base_class_code=PCI_CLASS.MEMORY_CONTROLLER,
            sub_class_coce=MEMORY_CONTROLLER_SUBCLASS.CXL_MEMORY_DEVICE,
            programming_interface=0x10,
        )
        pci_component = PciComponent(pci_identity, mmio_manager)

        # Create CxlMemoryDeviceComponent
        logger.debug(f"Total Capacity = {self._memory_size:x}")
        identity = MemoryDeviceIdentity()
        identity.fw_revision = MemoryDeviceIdentity.ascii_str_to_int("EEUM EMU 1.0", 16)
        identity.set_total_capacity(self._memory_size)
        identity.set_volatile_only_capacity(self._memory_size)
        self._cxl_memory_device_component = CxlMemoryDeviceComponent(
            identity,
            decoder_count=self._decoder_count,
            memory_file=self._memory_file,
            label=self._label,
        )

        # Create CombinedMmioRegister
        options = CombinedMmioRegiterOptions(cxl_component=self._cxl_memory_device_component)
        mmio_register = CombinedMmioRegister(options=options, parent_name="mmio")

        # Update MmioManager with new bar entires
        mmio_manager.set_bar_entries([BarEntry(register=mmio_register)])

        config_space_register_options = CxlType3SldConfigSpaceOptions(
            pci_component=pci_component,
            dvsec=DvsecConfigSpaceOptions(
                register_locator=DvsecRegisterLocatorOptions(
                    registers=mmio_register.get_dvsec_register_offsets()
                ),
                device_type=CXL_DEVICE_TYPE.LD,
                memory_device_component=self._cxl_memory_device_component,
            ),
            doe=CxlDoeExtendedCapabilityOptions(
                cdat_entries=self._cxl_memory_device_component.get_cdat_entries()
            ),
        )
        config_space_register = CxlType3SldConfigSpace(
            options=config_space_register_options, parent_name="cfgspace"
        )

        # ------------------------------
        # Update managers with registers
        # ------------------------------

        # Update ConfigSpaceManager with config space register
        config_space_manager.set_register(config_space_register)

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    async def _run(self):
        # pylint: disable=duplicate-code
        run_tasks = [
            create_task(self._cxl_io_manager.run()),
            create_task(self._cxl_mem_manager.run()),
        ]
        wait_tasks = [
            create_task(self._cxl_io_manager.wait_for_ready()),
            create_task(self._cxl_mem_manager.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        # pylint: disable=duplicate-code
        tasks = [
            create_task(self._cxl_io_manager.stop()),
            create_task(self._cxl_mem_manager.stop()),
        ]
        await gather(*tasks)
