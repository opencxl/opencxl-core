"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional

from opencxl.cxl.component.cxl_component import (
    CxlComponent,
    CXL_COMPONENT_TYPE,
)
from opencxl.cxl.component.hdm_decoder import (
    HdmDecoderManagerBase,
    SwitchHdmDecoderManager,
    HdmDecoderCapabilities,
    HDM_DECODER_COUNT,
)
from opencxl.cxl.component.cache_route_table_manager import (
    CacheRouteTableCapabilities,
    SwitchCacheRouteTable,
)
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable


class CxlUpstreamPortComponent(CxlComponent):
    def __init__(
        self,
        decoder_count: HDM_DECODER_COUNT = HDM_DECODER_COUNT.DECODER_1,
        label: Optional[str] = None,
    ):
        # pylint: disable=duplicate-code
        # CE-94
        super().__init__(label)
        hdm_decoder_capabilities = HdmDecoderCapabilities(
            decoder_count=decoder_count,
            target_count=8,
            a11to8_interleave_capable=0,
            a14to12_interleave_capable=0,
            poison_on_decoder_error_capability=0,
            three_six_twelve_way_interleave_capable=0,
            sixteen_way_interleave_capable=0,
            uio_capable=0,
            uio_capable_decoder_count=0,
            mem_data_nxm_capable=0,
        )
        self._hdm_decoder_manager = SwitchHdmDecoderManager(hdm_decoder_capabilities, label)
        cache_route_table_capabilities = CacheRouteTableCapabilities(
            cache_id_target_count=16,
            hdmd_type2_device_max_count=8,
            explicit_cache_id_rt_cmt_required=0,
        )
        self._cache_route_table = SwitchCacheRouteTable(cache_route_table_capabilities, label)
        self._routing_table = None

    def get_component_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.USP

    def get_hdm_decoder_manager(self) -> Optional[HdmDecoderManagerBase]:
        return self._hdm_decoder_manager

    def set_routing_table(self, routing_table: RoutingTable):
        self._routing_table = routing_table
        self._routing_table.set_hdm_decoder(self._hdm_decoder_manager)
        self._routing_table.set_cache_route_table(self._cache_route_table)


class CxlDownstreamPortComponent(CxlComponent):
    def get_component_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.DSP
