"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from typing import List
from opencxl.pci.component.pci import EEUM_VID, SW_SLD_DID, SW_MLD_DID


@dataclass(kw_only=True)
class LogicalDeviceConfig:
    serial_number: str
    port_index: int
    device_id: int
    vendor_id: int = EEUM_VID
    subsystem_vendor_id: int = 0
    subsystem_id: int = 0


@dataclass(kw_only=True)
class SingleLogicalDeviceConfig(LogicalDeviceConfig):
    memory_size: int  # in bytes
    memory_file: str
    device_id: int = SW_SLD_DID


@dataclass(kw_only=True)
class MultiLogicalDeviceConfig(LogicalDeviceConfig):
    memory_sizes: List[int]  # in bytes
    memory_files: List[str]
    ld_count: int
    device_id: int = SW_MLD_DID
