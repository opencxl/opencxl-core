"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task
from dataclasses import dataclass, field
from typing import List

from opencxl.cxl.component.physical_port_manager import (
    PhysicalPortManager,
    PortConfig,
    PORT_TYPE,
)
from opencxl.cxl.component.virtual_switch_manager import (
    VirtualSwitchManager,
    VirtualSwitchConfig,
)
from opencxl.cxl.component.virtual_switch.virtual_switch import (
    SwitchUpdateEvent,
)
from opencxl.cxl.component.switch_connection_manager import (
    SwitchConnectionManager,
    PortUpdateEvent,
)
from opencxl.cxl.component.mctp.mctp_connection_client import (
    MctpConnectionClient,
)
from opencxl.cxl.component.mctp.mctp_cci_executor import MctpCciExecutor
from opencxl.cxl.cci.generic.information_and_status import (
    BackgroundOperationStatusCommand,
)
from opencxl.cxl.cci.fabric_manager.physical_switch import (
    IdentifySwitchDeviceCommand,
    GetPhysicalPortStateCommand,
)
from opencxl.cxl.cci.fabric_manager.virtual_switch import (
    GetVirtualCxlSwitchInfoCommand,
    BindVppbCommand,
    UnbindVppbCommand,
)
from opencxl.cxl.cci.vendor_specfic import (
    NotifySwitchUpdateRequestPayload,
    NotifyPortUpdateRequestPayload,
    NotifyDeviceUpdateRequestPayload,
    GetConnectedDevicesCommand,
)
from opencxl.util.component import RunnableComponent
from opencxl.cxl.device.config.logical_device import SingleLogicalDeviceConfig, MultiLogicalDeviceConfig


@dataclass
class CxlSwitchConfig:
    port_configs: List[PortConfig] = field(default_factory=list)
    virtual_switch_configs: List[VirtualSwitchConfig] = field(default_factory=list)
    host: str = "0.0.0.0"
    port: int = 8000
    mctp_host: str = "0.0.0.0"
    mctp_port: int = 8100


class CxlSwitch(RunnableComponent):
    # TODO: CE-35, device enumeration from DSP is not supported yet.
    # Passing device configs from an environment file to PhysicalPortManager
    # as a workaround.
    def __init__(
        self,
        switch_config: CxlSwitchConfig,
        sld_configs: List[SingleLogicalDeviceConfig] = None,
        mld_configs: List[MultiLogicalDeviceConfig] = None,
    ):
        super().__init__()
        self._switch_connection_manager = SwitchConnectionManager(
            switch_config.port_configs, switch_config.host, switch_config.port
        )
        self._physical_port_manager = PhysicalPortManager(
            self._switch_connection_manager, switch_config.port_configs, sld_configs, mld_configs
        )
        self._virtual_switch_manager = VirtualSwitchManager(
            switch_config.virtual_switch_configs, self._physical_port_manager
        )
        self._mctp_connection_client = MctpConnectionClient(
            switch_config.mctp_host, switch_config.mctp_port
        )
        self._mctp_cci_executor = MctpCciExecutor(
            self._mctp_connection_client.get_mctp_connection()
        )
        self._initialize_mctp_endpoint()

    def _initialize_mctp_endpoint(self):
        commands = [
            BackgroundOperationStatusCommand(self._mctp_cci_executor),
            IdentifySwitchDeviceCommand(self._physical_port_manager, self._virtual_switch_manager),
            GetPhysicalPortStateCommand(self._switch_connection_manager),
            GetVirtualCxlSwitchInfoCommand(self._virtual_switch_manager),
            BindVppbCommand(self._physical_port_manager, self._virtual_switch_manager),
            UnbindVppbCommand(self._virtual_switch_manager),
            GetConnectedDevicesCommand(self._physical_port_manager),
        ]
        self._mctp_cci_executor.register_cci_commands(commands)

        async def handle_port_event(event: PortUpdateEvent):
            payload = NotifyPortUpdateRequestPayload(event.port_id, event.connected)
            request = payload.create_request()
            await self._mctp_cci_executor.send_notification(request)
            switch_ports = self._switch_connection_manager.get_switch_ports()
            if switch_ports[event.port_id].port_config.type == PORT_TYPE.DSP:
                payload = NotifyDeviceUpdateRequestPayload()
                request = payload.create_request()
                await self._mctp_cci_executor.send_notification(request)

        async def handle_switch_event(event: SwitchUpdateEvent):
            payload = NotifySwitchUpdateRequestPayload(
                event.vcs_id, event.vppb_id, event.binding_status
            )
            request = payload.create_request()
            await self._mctp_cci_executor.send_notification(request)

        self._switch_connection_manager.register_event_handler(handle_port_event)
        self._virtual_switch_manager.register_event_handler(handle_switch_event)

    async def _run(self):
        run_tasks = [
            create_task(self._switch_connection_manager.run()),
            create_task(self._physical_port_manager.run()),
            create_task(self._virtual_switch_manager.run()),
            create_task(self._mctp_connection_client.run()),
            create_task(self._mctp_cci_executor.run()),
        ]
        wait_tasks = [
            create_task(self._switch_connection_manager.wait_for_ready()),
            create_task(self._physical_port_manager.wait_for_ready()),
            create_task(self._virtual_switch_manager.wait_for_ready()),
            create_task(self._mctp_connection_client.wait_for_ready()),
            create_task(self._mctp_cci_executor.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        stop_tasks = [
            create_task(self._switch_connection_manager.stop()),
            create_task(self._physical_port_manager.stop()),
            create_task(self._virtual_switch_manager.stop()),
            create_task(self._mctp_connection_client.stop()),
            create_task(self._mctp_cci_executor.stop()),
        ]
        await gather(*stop_tasks)
