"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional

from opencxl.cxl.component.cxl_component import CxlComponent
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.bi_decoder import (
    CxlBIDecoderCapabilityStructureOptions,
    CxlBIDecoderCapabilityRegisterOptions,
    CxlBIDecoderControlRegisterOptions,
    CxlBIDecoderStatusRegisterOptions,
    CxlBIRTCapabilityStructureOptions,
    CxlBIRTCapabilityRegisterOptions,
    CxlBIRTControlRegisterOptions,
    CxlBIRTStatusRegisterOptions,
    CxlBITimeoutScale,
)
from opencxl.cxl.component.hdm_decoder import (
    HdmDecoderManagerBase,
    SwitchHdmDecoderManager,
    HdmDecoderCapabilities,
    HDM_DECODER_COUNT,
)
from opencxl.cxl.component.cache_route_table_manager import (
    SwitchCacheRouteTable,
)
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.mmio.component_register.memcache_register.cache_route_table import (
    CacheRouteTableCapabilityRegisterOptions,
)
from opencxl.cxl.mmio.component_register.memcache_register.cache_id_decoder_capability import (
    CxlCacheIdDecoderCapabilityRegisterOptions,
    CxlCacheIdDecoderCapabilityStructureOptions,
    CxlCacheIdDecoderControlOptions,
    CxlCacheIdDecoderStatusOptions,
)


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
            bi_capable=True,
        )
        self._hdm_decoder_manager = SwitchHdmDecoderManager(hdm_decoder_capabilities, label)
        self.cache_route_table_capabilities = CacheRouteTableCapabilityRegisterOptions(
            cache_id_target_count=16,
            hdmd_type2_device_max_count=8,
            explicit_cache_id_rt_cmt_required=0,
        )
        self._cache_route_table = SwitchCacheRouteTable(self.cache_route_table_capabilities, label)
        self._routing_table = None

    def get_component_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.USP

    def get_hdm_decoder_manager(self) -> Optional[HdmDecoderManagerBase]:
        return self._hdm_decoder_manager

    def get_bi_rt_options(self) -> Optional[CxlBIRTCapabilityStructureOptions]:
        options = CxlBIRTCapabilityStructureOptions()
        options["capability_options"] = CxlBIRTCapabilityRegisterOptions(
            explicit_bi_rt_commit_required=1
        )
        options["control_options"] = CxlBIRTControlRegisterOptions(bi_rt_commit=0)
        options["status_options"] = CxlBIRTStatusRegisterOptions(
            bi_rt_committed=0,
            bi_rt_error_not_committed=0,
            bi_rt_commit_timeout_base=CxlBITimeoutScale.hundred_ms,
            bi_rt_commit_timeout_scale=1,
        )
        return options

    def get_cache_route_table_options(self) -> Optional[CacheRouteTableCapabilityRegisterOptions]:
        return self.cache_route_table_capabilities

    def set_routing_table(self, routing_table: RoutingTable):
        self._routing_table = routing_table
        self._routing_table.set_hdm_decoder(self._hdm_decoder_manager)
        self._routing_table.set_cache_route_table(self._cache_route_table)


class CxlDownstreamPortComponent(CxlComponent):
    def __init__(
        self,
        label: Optional[str] = None,
        cache_id_decoder_options: Optional[CxlCacheIdDecoderCapabilityStructureOptions] = None,
    ):
        if cache_id_decoder_options:
            self.cache_id_decoder_options = cache_id_decoder_options
        else:
            # assign a sensible default
            register_options = CxlCacheIdDecoderCapabilityRegisterOptions(
                explicit_cache_id_decoder_cmt_required=0, rsvd=0
            )
            control_options = CxlCacheIdDecoderControlOptions(
                forward_cache_id=1,
                assign_cache_id=0,
                hdmd_t2_device_present=0,
                cache_id_decoder_cmt=0,
                rsvd=0,
                hdmd_t2_device_cache_id=0,
                rsvd2=0,
                local_cache_id=0,
                rsvd3=0,
            )
            status_options = CxlCacheIdDecoderStatusOptions(
                cache_id_decoder_cmtd=0,
                cache_id_decoder_err_not_cmtd=0,
                rsvd=0,
                cache_id_decoder_cmt_timeout_scale=0,
                cache_id_decoder_cmt_timeout_base=0,
                rsvd2=0,
            )
            self.cache_id_decoder_options = CxlCacheIdDecoderCapabilityStructureOptions(
                register_options=register_options,
                control_options=control_options,
                status_options=status_options,
            )
        super().__init__()

    def get_bi_decoder_options(self) -> Optional[CxlBIDecoderCapabilityStructureOptions]:
        options = CxlBIDecoderCapabilityStructureOptions()
        options["capability_options"] = CxlBIDecoderCapabilityRegisterOptions(
            explicit_bi_decoder_commit_required=1
        )
        options["control_options"] = CxlBIDecoderControlRegisterOptions(
            bi_forward=1,
            bi_enable=0,
            bi_decoder_commit=0,
        )
        options["status_options"] = CxlBIDecoderStatusRegisterOptions(
            bi_decoder_committed=0,
            bi_decoder_error_not_committed=0,
            bi_decoder_commit_timeout_base=CxlBITimeoutScale.hundred_ms,
            bi_decoder_commit_timeout_scale=1,
        )
        options["device_type"] = self.get_component_type()
        return options

    def get_cache_decoder_options(self) -> Optional[CxlCacheIdDecoderCapabilityStructureOptions]:
        return self.cache_id_decoder_options

    def get_component_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.DSP
