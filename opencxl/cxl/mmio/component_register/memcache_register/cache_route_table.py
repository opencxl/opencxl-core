"""
CXL Cache ID Route Table Capability Structure definitions.
"""

from enum import Enum
from typing import Optional, TypedDict

from opencxl.util.unaligned_bit_structure import (
    ShareableByteArray,
    BitMaskedBitStructure,
    BitField,
    StructureField,
    ByteField,
    FIELD_ATTR,
)


class CacheIdRTCommitTimeout(Enum):
    _1_uS = 0b0000
    _10_uS = 0b0001
    _100_uS = 0b0010
    _1_mS = 0b0011
    _10_mS = 0b0100
    _100_mS = 0b0101
    _1_S = 0b0110
    _10_S = 0b0111


class CacheRouteTableCapabilityRegisterOptions(TypedDict):
    # pylint: disable=duplicate-code
    cache_id_target_count: int
    rsvd: int
    hdmd_type2_dev_max_count: int
    rsvd2: int
    explicit_cache_id_rt_cmt_req: int
    rsvd3: int


class CacheRouteTableControlRegisterOptions(TypedDict):
    cache_id_rt_cmt: int
    rsvd: int


class CacheRouteTableStatusRegisterOptions(TypedDict):
    cache_id_rt_cmtd: int
    cache_id_rt_err_not_cmtd: int
    rsvd: int
    cache_id_rt_cmt_timeout_scale: CacheIdRTCommitTimeout
    cache_id_rt_cmt_timeout_base: int
    rsvd2: int


class CacheIdTargetNOptions(TypedDict):
    valid: int
    rsvd: int
    port_number: int


# Note the difference between CacheRouteTableCapabilityStructure & CacheRouteTableCapabilityRegister
class CacheRouteTableCapabilityStructureOptions(TypedDict, total=False):
    register_options: CacheRouteTableCapabilityRegisterOptions
    control_options: CacheRouteTableControlRegisterOptions
    status_options: CacheRouteTableStatusRegisterOptions
    target0_options: CacheIdTargetNOptions
    target1_options: CacheIdTargetNOptions
    target2_options: CacheIdTargetNOptions
    target3_options: CacheIdTargetNOptions
    target4_options: CacheIdTargetNOptions
    target5_options: CacheIdTargetNOptions
    target6_options: CacheIdTargetNOptions
    target7_options: CacheIdTargetNOptions
    target8_options: CacheIdTargetNOptions
    target9_options: CacheIdTargetNOptions
    target10_options: CacheIdTargetNOptions
    target11_options: CacheIdTargetNOptions
    target12_options: CacheIdTargetNOptions
    target13_options: CacheIdTargetNOptions
    target14_options: CacheIdTargetNOptions
    target15_options: CacheIdTargetNOptions


class CxlCacheIdRTCapability(BitMaskedBitStructure):
    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CacheRouteTableCapabilityStructureOptions] = None,
    ):
        # pylint: disable=duplicate-code
        self._fields = [
            BitField("cache_id_target_count", 0, 4),
            BitField("rsvd", 5, 7, FIELD_ATTR.RESERVED),
            BitField("hdmd_type2_dev_max_count", 8, 11),
            BitField("rsvd2", 12, 15, FIELD_ATTR.RESERVED),
            BitField("explicit_cache_id_rt_cmt_req", 16, 16),
            BitField("rsvd3", 17, 31, FIELD_ATTR.RESERVED),
        ]

        if data:
            super().__init__(data, parent_name)
            return

        super().__init__(parent_name=parent_name)
        for reg, val in options["register_options"].items():
            setattr(self, reg, val)


class CxlCacheIdRTControl(BitMaskedBitStructure):
    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CacheRouteTableCapabilityStructureOptions] = None,
    ):
        # pylint: disable=duplicate-code
        cache_id_rt_cmt_attr: FIELD_ATTR
        if options["register_options"]["explicit_cache_id_rt_cmt_req"]:
            cache_id_rt_cmt_attr = FIELD_ATTR.RW
        else:
            cache_id_rt_cmt_attr = FIELD_ATTR.RESERVED

        # Must be defined within the scope of __init__, since the attributes
        # of cache_id_rt_cmt may differ between instances

        self.fields = [
            BitField("cache_id_rt_cmt", 0, 0, cache_id_rt_cmt_attr),
            BitField("rsvd", 1, 31, FIELD_ATTR.RESERVED),
        ]

        if data:
            super().__init__(data, parent_name)
            return

        super().__init__(parent_name=parent_name)
        for reg, val in options["control_options"].items():
            setattr(self, reg, val)


class CxlCacheIdRTStatus(BitMaskedBitStructure):
    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CacheRouteTableCapabilityStructureOptions] = None,
    ):
        # pylint: disable=duplicate-code
        cache_id_rt_cmt_attr: FIELD_ATTR
        if options["register_options"]["explicit_cache_id_rt_cmt_req"]:
            cache_id_rt_cmt_attr = FIELD_ATTR.RW
        else:
            cache_id_rt_cmt_attr = FIELD_ATTR.RESERVED

        self._fields = [
            BitField("cache_id_rt_cmtd", 0, 0, cache_id_rt_cmt_attr),
            BitField("cache_id_rt_err_not_cmtd", 1, 1, cache_id_rt_cmt_attr),
            BitField("rsvd", 2, 7, FIELD_ATTR.RESERVED),
            BitField("cache_id_rt_cmt_timeout_scale", 8, 11, cache_id_rt_cmt_attr),
            BitField("cache_id_rt_cmt_timeout_base", 12, 15, cache_id_rt_cmt_attr),
            BitField("rsvd2", 16, 31, FIELD_ATTR.RESERVED),
        ]

        if data:
            super().__init__(data, parent_name)
            return

        super().__init__(parent_name=parent_name)
        for reg, val in options["status_options"].items():
            setattr(self, reg, val)


class CxlCacheIdRTTargetN(BitMaskedBitStructure):
    _fields = [BitField("valid", 0, 0), BitField("rsvd", 1, 7), BitField("port_number", 8, 15)]


class CxlCacheIdRTCapabilityStructure(BitMaskedBitStructure):
    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CacheRouteTableCapabilityStructureOptions] = None,
    ):
        if not options:
            raise ValueError("options is required")
        self._init_global(options)
        self._init_target_entries(options)

        super().__init__(data, parent_name)

    def _init_global(self, options: CacheRouteTableCapabilityStructureOptions):
        self._fields = [
            StructureField(
                "cxl_cache_id_rt_capability", 0x00, 0x03, CxlCacheIdRTCapability, options=options
            ),
            StructureField(
                "cxl_cache_id_rt_control", 0x04, 0x07, CxlCacheIdRTControl, options=options
            ),
            StructureField(
                "cxl_cache_id_rt_status", 0x08, 0x0B, CxlCacheIdRTStatus, options=options
            ),
            ByteField("rsvd", 0x0C, 0x0F, FIELD_ATTR.RESERVED),
        ]

    def _init_target_entries(self, options: CacheRouteTableCapabilityStructureOptions):
        for target_idx in options["register_options"]["cache_id_target_count"]:
            tgt_opts = options[f"target{target_idx}_options"]
            self._init_single_target(target_idx, tgt_opts)

    def _init_single_target(self, cache_id: int, options: CacheIdTargetNOptions):
        self._fields += StructureField(
            f"target_{cache_id}",
            0x10 + (2 * cache_id),
            0x11 + (2 * cache_id),
            CxlCacheIdRTTargetN,
            options=CacheIdTargetNOptions(
                options=options,
            ),
        )

    @staticmethod
    def get_size_from_options(options: Optional[CacheRouteTableCapabilityStructureOptions]):
        return 0x10 + (2 * options["register_options"]["cache_id_target_count"])
