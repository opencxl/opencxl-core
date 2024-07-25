"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import List
from dataclasses import dataclass, field
from opencxl.util.component import LabeledComponent, Label
from opencxl.cxl.component.root_complex.root_complex import RootComplex
from opencxl.drivers.cxl_bus_driver import CxlBusDriver, CxlDeviceInfo
from opencxl.util.logger import logger


class CxlMemDriver(LabeledComponent):
    def __init__(
        self, cxl_bus_driver: CxlBusDriver, root_complex: RootComplex, label: Label = None
    ):
        super().__init__(label)
        self._root_complex = root_complex
        self._cxl_bus_driver = cxl_bus_driver
        self._devices: List[CxlDeviceInfo] = []
        self.scan_mem_devices()

    def scan_mem_devices(self):
        self._devices = []
        for device in self._cxl_bus_driver.get_devices():
            if device.device_dvsec:
                self._devices.append(device)

    def get_devices(self):
        return self._devices

    async def attach_single_mem_device(
        self, device: CxlDeviceInfo, hpa_base: int, size: int
    ) -> bool:
        device.log_prefix = "CxlMemDriver"
        successful = await device.configure_hdm_decoder_device(hpa_base=hpa_base, hpa_size=size)
        if not successful:
            bdf_str = device.pci_device_info.get_bdf_string()
            logger.warning(self._create_message(f"Failed to configure HDM decoder of {bdf_str}"))
            return False

        downstream_port = device.parent
        if not downstream_port.is_downstream_port():
            bdf_str = downstream_port.pci_device_info.get_bdf_string()
            logger.warning(self._create_message(f"{bdf_str} is not upstream port"))
            return False
        port_number = downstream_port.pci_device_info.get_port_number()

        upstream_port = downstream_port.parent
        upstream_port.log_prefix = "CxlMemDriver"
        if not upstream_port.is_upstream_port():
            bdf_str = upstream_port.pci_device_info.get_bdf_string()
            logger.warning(self._create_message(f"{bdf_str} is not upstream port"))
            return False

        successful = await upstream_port.configure_hdm_decoder_switch(
            hpa_base=hpa_base, hpa_size=size, target_list=[port_number]
        )
        if not successful:
            bdf_str = device.pci_device_info.get_bdf_string()
            logger.warning(self._create_message(f"Failed to configure HDM decoder of {bdf_str}"))
            return False
        return True
