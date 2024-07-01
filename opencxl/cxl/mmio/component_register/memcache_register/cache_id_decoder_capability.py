"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from opencxl.util.unaligned_bit_structure import (
    BitMaskedBitStructure,
    BitField,
)


class CxlCacheIdDecoderCapability(BitMaskedBitStructure):
    _fields = [
        BitField("explicit_cache_id_decoder_cmt_required", 0, 0),
        BitField("rsvd", 1, 31),
    ]


class CxlCacheIdDecoderControl(BitMaskedBitStructure):
    _fields = [
        BitField("forward_cache_id", 0, 0),
        BitField("assign_cache_id", 1, 1),
        BitField("hdmd_t2_device_present", 2, 2),
        BitField("cache_id_decoder_cmt", 3, 3),
        BitField("rsvd", 4, 7),
        BitField("hdmd_t2_device_cache_id", 8, 11),
        BitField("rsvd2", 12, 15),
        BitField("local_cache_id", 16, 19),
        BitField("rsvd3", 20, 31),
    ]


class CxlCacheIdDecoderStatus(BitMaskedBitStructure):
    _fields = [
        BitField("cache_id_decoder_cmtd", 0, 0),
        BitField("cache_id_decoder_err_not_cmtd", 1, 1),
        BitField("rsvd", 2, 7),
        BitField("cache_id_decoder_cmt_timeout_scale", 8, 11),
        BitField("cache_id_decoder_cmt_timeout_base", 12, 15),
        BitField("rsvd2", 16, 31),
    ]
