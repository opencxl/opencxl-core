"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass, field
from typing import List
import humanfriendly
import yaml

from opencxl.apps.cxl_switch import (
    CxlSwitchConfig,
    VirtualSwitchConfig,
    PortConfig,
)
from opencxl.cxl.component.cxl_component import PORT_TYPE
from opencxl.cxl.device.config.logical_device import SingleLogicalDeviceConfig


@dataclass
class CxlEnvironment:
    switch_config: CxlSwitchConfig
    single_logical_device_configs: List[SingleLogicalDeviceConfig] = field(default_factory=list)


def parse_switch_config(config_data) -> CxlSwitchConfig:
    if "port_configs" not in config_data or not isinstance(config_data["port_configs"], list):
        raise ValueError("Missing or invalid 'port_configs' in configuration data.")

    switch_config = CxlSwitchConfig(
        host=config_data.get("host", "0.0.0.0"), port=config_data.get("port", 8000)
    )

    for port in config_data["port_configs"]:
        if "type" not in port:
            raise ValueError("Missing 'type' for 'port_config' entry.")
        if port["type"] not in ["USP", "DSP"]:
            raise ValueError(
                f"Invalid 'type' value for 'port_config': {port['type']}. Expected 'USP' or 'DSP'."
            )

        port_type = PORT_TYPE[port["type"]]
        switch_config.port_configs.append(PortConfig(type=port_type))

    if "virtual_switch_configs" not in config_data or not isinstance(
        config_data["virtual_switch_configs"], list
    ):
        raise ValueError("Missing or invalid 'virtual_switch_configs' in configuration data.")

    for vswitch in config_data["virtual_switch_configs"]:
        try:
            switch_config.virtual_switch_configs.append(
                VirtualSwitchConfig(
                    upstream_port_index=vswitch["upstream_port_index"],
                    vppb_counts=vswitch["vppb_counts"],
                    initial_bounds=vswitch["initial_bounds"],
                )
            )
        except KeyError as e:
            raise ValueError(f"Missing {e.args[0]} for 'virtual_switch_config' entry.") from e

    return switch_config


def parse_single_logical_device_configs(
    devices_data,
) -> List[SingleLogicalDeviceConfig]:
    if not isinstance(devices_data, list):
        raise ValueError("Invalid 'devices' configuration, expected a list.")

    single_logical_device_configs = []
    for device in devices_data:
        try:
            port_index = device["port_index"]
        except KeyError as exc:
            raise ValueError("Missing 'port_index' for 'device' entry.") from exc

        memory_file = device.get("memory_file", f"mem{port_index}.bin")

        try:
            memory_size = humanfriendly.parse_size(device["memory_size"], binary=True)
        except KeyError as exc:
            raise ValueError("Missing 'memory_size' for 'device' entry.") from exc
        except humanfriendly.InvalidSize as exc:
            raise ValueError(f"Invalid 'memory_size' value: {device['memory_size']}") from exc

        try:
            serial_number = device["serial_number"]
        except KeyError as exc:
            raise ValueError("Missing 'serial_number' for 'device' entry.") from exc

        single_logical_device_configs.append(
            SingleLogicalDeviceConfig(
                port_index=port_index,
                memory_size=memory_size,
                memory_file=memory_file,
                serial_number=serial_number,
            )
        )
    return single_logical_device_configs


def parse_cxl_environment(yaml_path: str) -> CxlEnvironment:
    with open(yaml_path, "r") as file:
        config_data = yaml.safe_load(file)

    if not config_data:
        raise ValueError("Configuration file is empty or has invalid content.")

    switch_config = parse_switch_config(config_data)
    single_logical_device_configs = parse_single_logical_device_configs(
        config_data.get("devices", [])
    )

    return CxlEnvironment(
        switch_config=switch_config,
        single_logical_device_configs=single_logical_device_configs,
    )
