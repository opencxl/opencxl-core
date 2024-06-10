"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=duplicate-code, unused-import
from asyncio import create_task, gather
from dataclasses import dataclass
from typing import Optional

from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
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


class CxlType1Device(RunnableComponent):
    def __init__(
        self,
        transport_connection: CxlConnection,
        label: Optional[str] = None,
    ):
        super().__init__(label)
        self._cxl_cache_device_component = None
        self._upstream_connection = transport_connection

        self._cxl_io_manager = CxlIoManager(
            self._upstream_connection.mmio_fifo,
            None,
            self._upstream_connection.cfg_fifo,
            None,
            device_type=PCI_DEVICE_TYPE.ENDPOINT,
            init_callback=self._init_device,
            label=self._label,
        )
        self._cxl_cache_manager = CxlCacheManager(
            upstream_fifo=self._upstream_connection.cxl_cache_fifo,
            label=self._label,
        )

        # TODO: Update CxlCacheManager with a CxlCacheDeviceComponent
        # self._cxl_cache_manager.set_cache_device_component(self._cxl_cache_device_component)

    def _init_device(
        self,
        mmio_manager: MmioManager,
    ):
        # pylint: disable=unused-variable
        # Create PCiComponent, which will be used in the future
        pci_identity = PciComponentIdentity(
            vendor_id=EEUM_VID,
            device_id=SW_SLD_DID,
            base_class_code=PCI_CLASS.MEMORY_CONTROLLER,
            sub_class_coce=MEMORY_CONTROLLER_SUBCLASS.CXL_MEMORY_DEVICE,
            programming_interface=0x10,
        )
        pci_component = PciComponent(pci_identity, mmio_manager)

        # TODO: Create CxlCacheDeviceComponent
        # self._cxl_cache_device_component = CxlCacheDeviceComponent(
        #     decoder_count=self._decoder_count,
        #     label=self._label,
        # )

        # Create CombinedMmioRegister
        options = CombinedMmioRegiterOptions(cxl_component=self._cxl_cache_device_component)
        mmio_register = CombinedMmioRegister(options=options, parent_name="mmio")

        # Update MmioManager with new bar entires
        mmio_manager.set_bar_entries([BarEntry(register=mmio_register)])

        # TODO: Future CFG is needed

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    async def _run(self):
        # pylint: disable=duplicate-code
        run_tasks = [
            create_task(self._cxl_io_manager.run()),
            create_task(self._cxl_cache_manager.run()),
        ]
        wait_tasks = [
            create_task(self._cxl_io_manager.wait_for_ready()),
            create_task(self._cxl_cache_manager.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        # pylint: disable=duplicate-code
        tasks = [
            create_task(self._cxl_io_manager.stop()),
            create_task(self._cxl_cache_manager.stop()),
        ]
        await gather(*tasks)
