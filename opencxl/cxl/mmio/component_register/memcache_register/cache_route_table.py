"""
CXL Cache ID Route Table Capability Structure definitions.
"""
from enum import Enum
from opencxl.util.unaligned_bit_structure import (
    BitMaskedBitStructure,
    BitField,
    StructureField,
    ByteField,
    FIELD_ATTR,
    RepeatedDynamicField,
)


# TODO: can probably optimize conversion to a single function/hashmap/list
class CacheIdRtCommitTimeout(Enum):
    _1_uS = 0b0000
    _10_uS = 0b0001
    _100_uS = 0b0010
    _1_mS = 0b0011
    _10_mS = 0b0100
    _100_mS = 0b0101
    _1_S = 0b0110
    _10_S = 0b0111


class CxlCacheIdRTCapability(BitMaskedBitStructure):
    _fields = [
        BitField("cache_id_target_count", 0, 4),
        BitField("rsvd", 5, 7),
        BitField("hdmd_type2_dev_max_count", 8, 11),
        BitField("rsvd2", 12, 15),
        BitField("explicit_cache_id_rt_cmt_req", 16, 16),
        BitField("rsvd3", 17, 31),
    ]


class CxlCacheIdRTControl(BitMaskedBitStructure):
    _fields = [BitField("cache_id_rt_cmt", 0, 0), BitField("rsvd", 1, 31)]


class CxlCacheIdRTStatus(BitMaskedBitStructure):
    _fields = [
        BitField("cache_id_rt_cmtd", 0, 0),
        BitField("cache_id_rt_err_not_cmtd", 1, 1),
        BitField("rsvd", 2, 7),
        BitField("cache_id_rt_cmt_timeout_scale", 8, 11),
        BitField("cache_id_rt_cmt_timeout_base", 12, 15),
        BitField("rsvd2", 16, 31),
    ]


class CxlCacheIdRTTargetN(BitMaskedBitStructure):
    _fields = [BitField("valid", 0, 0), BitField("rsvd", 1, 7), BitField("port_number", 8, 15)]


class CxlCacheIdRTCapabilityStructure(BitMaskedBitStructure):
    _fields = [
        StructureField("cxl_cache_id_rt_capability", 0x00, 0x03, CxlCacheIdRTCapability),
        StructureField("cxl_cache_id_rt_control", 0x04, 0x07, CxlCacheIdRTControl),
        StructureField("cxl_cache_id_rt_status", 0x08, 0x0B, CxlCacheIdRTStatus),
        ByteField("rsvd", 0x0C, 0x0F, FIELD_ATTR.RESERVED),
        RepeatedDynamicField(
            "cache_targets",
            0x10,
            0,
            _underlying_struct=StructureField("target_n", 0, 1, CxlCacheIdRTTargetN),
        ),
    ]
