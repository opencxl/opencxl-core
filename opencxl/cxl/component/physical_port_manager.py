"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from dataclasses import dataclass
from typing import List, Dict, Optional, cast

from opencxl.cxl.device.port_device import CxlPortDevice
from opencxl.cxl.device.upstream_port_device import UpstreamPortDevice
from opencxl.cxl.device.downstream_port_device import DownstreamPortDevice
from opencxl.cxl.device.config.logical_device import SingleLogicalDeviceConfig
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.cxl_component import (
    PORT_TYPE,
    PortConfig,
    CXL_COMPONENT_TYPE,
)
from opencxl.util.component import RunnableComponent


@dataclass
class MemoryDeviceInfo:
    vendor_id: int
    device_id: int
    subsystem_vendor_id: int
    subsystem_id: int
    serial_number: str
    bound_port_id: int
    total_capacity: int


# TODO: Support initializing USP hdm decoder count
class PhysicalPortManager(RunnableComponent):
    def __init__(
        self,
        switch_connection_manager: SwitchConnectionManager,
        port_configs: List[PortConfig],
        # TODO: CE-35, device enumeration from DSP is not supported yet.
        # Read device configs from an environment file directly as a workaround.
        device_configs: Optional[List[SingleLogicalDeviceConfig]] = None,
    ):
        super().__init__()
        self._port_devices: List[CxlPortDevice] = []
        self._switch_connection_manager = switch_connection_manager
        self._device_configs = device_configs
        for port_index, port_config in enumerate(port_configs):
            transport_connection = self._switch_connection_manager.get_cxl_connection(port_index)
            if port_config.type == PORT_TYPE.USP:
                self._port_devices.append(UpstreamPortDevice(transport_connection, port_index))
            else:
                self._port_devices.append(DownstreamPortDevice(transport_connection, port_index))

    def get_port_device(self, port_index: int) -> CxlPortDevice:
        if port_index < 0 or port_index >= len(self._port_devices):
            raise Exception(f"port index {port_index} is out of bound")
        return self._port_devices[port_index]

    def get_port_counts(self) -> int:
        return len(self._port_devices)

    def get_port_devices(self) -> List[CxlPortDevice]:
        return self._port_devices

    def get_usp_hdm_decoder_count(self) -> int:
        hdm_decoder_count = 0
        for port in self._port_devices:
            if port.get_device_type() == CXL_COMPONENT_TYPE.USP:
                usp = cast(UpstreamPortDevice, port)
                hdm_decoder_count = usp.get_hdm_decoder_count()
        return hdm_decoder_count

    def get_connected_devices(self) -> List[MemoryDeviceInfo]:
        if self._device_configs is None:
            return []

        # TODO: CE-35, device enumeration from DSP is not supported yet.
        # This is a temporary implementation that finds connected device's info
        # from an environment file.
        device_configs_by_port_id: Dict[int, SingleLogicalDeviceConfig] = {}
        for device_config in self._device_configs:
            device_configs_by_port_id[device_config.port_index] = device_config

        connected_devices = []
        switch_ports = self._switch_connection_manager.get_switch_ports()
        for port_index, switch_port in enumerate(switch_ports):
            if switch_port.port_config.type != PORT_TYPE.DSP:
                continue
            if port_index not in device_configs_by_port_id:
                raise Exception(f"Device config for port {port_index} is not found")
            switch_config = device_configs_by_port_id[port_index]
            switch_port = switch_ports[port_index]
            if switch_port.connected:
                device_info = MemoryDeviceInfo(
                    vendor_id=switch_config.vendor_id,
                    device_id=switch_config.device_id,
                    subsystem_vendor_id=switch_config.subsystem_vendor_id,
                    subsystem_id=switch_config.subsystem_id,
                    serial_number=switch_config.serial_number,
                    bound_port_id=port_index,
                    total_capacity=switch_config.memory_size,
                )
                connected_devices.append(device_info)
        return connected_devices

    async def _run(self):
        run_tasks = []
        wait_tasks = []
        for port_device in self._port_devices:
            run_tasks.append(create_task(port_device.run()))
            wait_tasks.append(create_task(port_device.wait_for_ready()))
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        tasks = []
        for port_device in self._port_devices:
            tasks.append(create_task(port_device.stop()))
        await gather(*tasks)
