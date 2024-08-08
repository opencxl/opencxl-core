"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from opencxl.pci.component.pci import EEUM_VID, SW_SLD_DID, SW_MLD_DID
from typing import List


@dataclass
class SingleLogicalDeviceConfig:
    serial_number: str
    port_index: int
    memory_size: int  # in bytes
    memory_file: str
    vendor_id: int = EEUM_VID
    device_id: int = SW_SLD_DID
    subsystem_vendor_id: int = 0
    subsystem_id: int = 0


@dataclass
class MultiLogicalDeviceConfig:
    serial_number: List[str]
    port_index: int
    ld_indexes: List[int]
    memory_size: List[int]  # in bytes
    memory_file: List[str]
    vendor_id: int = EEUM_VID
    device_id: int = SW_MLD_DID
    subsystem_vendor_id: int = 0
    subsystem_id: int = 0
