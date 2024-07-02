"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import pytest

from opencxl.util.unaligned_bit_structure import (
    UnalignedBitStructure,
    ByteField,
    StructureField,
    DynamicByteField,
    RepeatedDynamicField,
)

from opencxl.cxl.transport.transaction import (
    CxlIoMemWrPacket,
)


class DynamicByteStructure(UnalignedBitStructure):
    field1: int
    field2: int
    field3: int
    payload: int
    _fields = [
        ByteField("field1", 0, 0),  # 1 Byte
        ByteField("field2", 1, 2),  # 2 Bytes
        ByteField("field3", 3, 5),  # 3 Bytes
        DynamicByteField("payload", 6, 1),
    ]


class MiniStructure(UnalignedBitStructure):
    field1: int
    field2: int
    field3: int
    _fields = [
        ByteField("field1", 0, 0),  # 1 Byte
        ByteField("field2", 1, 2),  # 2 Bytes
        ByteField("field3", 3, 5),  # 3 Bytes
    ]


class BigTabularStructure(UnalignedBitStructure):
    field1: int
    field2: int
    table: int
    _fields = [
        ByteField("field1", 0, 1),  # 2 Bytes
        ByteField("field2", 2, 4),  # 3 Bytes
        RepeatedDynamicField(
            "table", 5, 18, _underlying_struct=StructureField("subtable", 0, 5, MiniStructure)
        ),  # 18 Bytes
    ]


def test_basic_tabular():
    raw_data = bytes(
        [
            0x00,
            0x01,
            0x02,
            0x03,
            0x04,
            0x05,
            0x06,
            0x07,
            0x08,
            0x09,
            0x0A,
            0x0B,
            0x0C,
            0x0D,
            0x0E,
            0x0F,
            0x10,
            0x11,
            0x12,
            0x13,
            0x14,
            0x15,
            0x16,
        ]
    )
    tabular = BigTabularStructure()
    tabular.reset(raw_data)
    assert tabular.field1 == 0x0100
    assert tabular.table[0].field1 == 0x05
    assert tabular.table[1].field2 == 0x0D0C
    assert len(tabular.table) == 3

    raw_data_smaller = bytes(
        [
            0x00,
            0x01,
            0x02,
            0x03,
            0x04,
            0x10,
            0x0F,
            0x0E,
            0x0D,
            0x0C,
            0x0B,
            0x0A,
            0x09,
            0x08,
            0x07,
            0x06,
            0x05,
        ]
    )
    tabular.reset(raw_data_smaller)  # should automatically resize
    assert len(tabular.table) == 2
    assert tabular.table[0].field2 == 0x0E0F
    assert tabular.table[1].field3 == 0x050607


def test_basic_dbf():
    pckt = bytes([35, 25, 85, 90, 15, 100, 200, 210, 95])
    DBS = DynamicByteStructure()
    DBS.reset(pckt)
    assert DBS.field1 == 0x23
    assert DBS.field2 == 0x5519
    assert DBS.field3 == 0x640F5A
    assert DBS.payload == 0x5FD2C8
    assert len(DBS) == len(pckt)

    pckt2 = bytes([1, 1, 1, 1, 1, 1, 2, 3])
    DBS.reset(pckt2)
    assert DBS.field3 == 0x10101
    assert DBS.payload == 0x302
    assert len(DBS) == len(pckt2)

    pckt3 = bytes([1, 1, 1, 1, 1, 1, 8, 16, 24, 32, 110, 251])
    DBS.reset(pckt3)
    assert DBS.field3 == 0x10101
    assert DBS.payload == 0xFB6E20181008
    assert len(DBS) == len(pckt3)


class DisallowedDyBStruct(UnalignedBitStructure):
    field1: int
    field2: int
    field3: int
    payload: int
    _fields = [
        ByteField("field1", 0, 0),  # 1 Byte
        ByteField("field2", 1, 2),  # 2 Bytes
        ByteField("field3", 3, 5),  # 3 Bytes
        DynamicByteField("payload", 6, 2),
        DynamicByteField("payload2", 8, 4),
    ]


class DisallowedDyBStruct2(UnalignedBitStructure):
    field1: int
    field2: int
    field3: int
    payload: int
    _fields = [
        ByteField("field1", 0, 0),  # 1 Byte
        ByteField("field2", 1, 2),  # 2 Bytes
        DynamicByteField("payload", 3, 2),
        ByteField("field3", 5, 8),  # 4 Bytes
    ]


def test_unpleasant_dbf_failure():
    # pylint: disable=unused-variable
    with pytest.raises(Exception) as exc_info:
        DBS_Bad = DisallowedDyBStruct()
    assert (
        exc_info.value.args[0]
        == "The current implementation does not allow for more than one dynamic byte field."
    )

    with pytest.raises(Exception) as exc_info:
        DBS_Bad2 = DisallowedDyBStruct2()
    assert (
        exc_info.value.args[0][-68:]
        == "DynamicByteFields must be the last field in their respective packets"
    )


def test_io_mem_wr():
    addr = 0x0
    data = 0xDEADBEEF
    packet = CxlIoMemWrPacket.create(addr, 4, data=data)
    assert packet.data == data
