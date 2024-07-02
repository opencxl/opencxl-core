"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from typing import TypedDict, List

from opencxl.util.component import LabeledComponent
from opencxl.util.logger import logger


class CacheRouteTableCapabilities(TypedDict):
    # pylint: disable=duplicate-code
    cache_id_target_count: int
    hdmd_type2_device_max_count: int
    explicit_cache_id_rt_cmt_required: int


@dataclass
class CacheRouteTableBase(LabeledComponent):
    _capabilities: CacheRouteTableCapabilities
    _target_count: int
    _cache_id_to_port_mapping: List[int]

    def __init__(self, capabilities, label):
        super().__init__(label)
        self._capabilities = capabilities
        self._target_count = capabilities["cache_id_target_count"]
        self._cache_id_to_port_mapping = [0] * self._target_count

    def get_target(self, cache_id) -> int:
        return self._cache_id_to_port_mapping[cache_id]


@dataclass
class SwitchCacheRouteTable(CacheRouteTableBase):
    """
    For a CXL Switch, we have 16 cache route table entries (for 256B flit mode).
    """

    def __init__(self, capabilities, label):
        if capabilities["cache_id_target_count"] != 0x10:
            raise ValueError(
                "CXL switch routing table must have 16 cache route table entries "
                f"but {capabilities.cache_id_target_count} were given"
            )  # TODO: support 68B flit mode?
        super().__init__(capabilities, label)

    # TODO: properly implement commits
    def commit(self, index: int, new_port) -> bool:
        if index > len(self._cache_id_to_port_mapping):
            logger.warning(f"Cache ID ({index}) is out of bound")
            return False

        self._cache_id_to_port_mapping[index] = new_port

        decoder_commit_info = (
            f"[Cache Route Table Commit] Cache ID: {index}, Mapped Port: {new_port}"
        )
        logger.info(self._create_message(decoder_commit_info))
        return True
