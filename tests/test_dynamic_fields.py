"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from opencxl.util.unaligned_bit_structure import (
    UnalignedBitStructure,
    BitField,
    ByteField,
    DynamicByteField,
    StructureField,
)

class DynamicByteStructure:
    field1: int
    field2: int
    field3: int
    payload: int
    _fields = [
        ByteField("field1", 0, 0),  # 1 Byte
        ByteField("field2", 1, 2),  # 2 Bytes
        ByteField("field3", 3, 5),  # 3 Bytes
        DynamicByteField("payload", 6, 4),  # 4 Bytes
    ]

def test_basic_dbf():
    pass
