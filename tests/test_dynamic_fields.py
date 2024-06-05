"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from opencxl.util.unaligned_bit_structure import (
    UnalignedBitStructure,
    ByteField,
    DynamicByteField,
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
        DynamicByteField("payload", 6, 0)
    ]

def test_basic_dbf():
    pckt = bytes([35, 25, 85, 90, 15, 100, 200, 210, 95])
    DBS = DynamicByteStructure()
    DBS.reset(pckt)
    assert DBS.field1 == 0x23
    assert DBS.field2 == 0x5519
    assert DBS.field3 == 0x640f5a
    assert DBS.payload == 0x5fd2c8
    assert len(DBS) == len(pckt)

    pckt2 = bytes([1, 1, 1, 1, 1, 1, 2, 3])
    DBS.reset(pckt2)
    assert DBS.field3 == 0x10101
    assert DBS.payload == 0x302
    assert len(DBS) == len(pckt2)

    pckt3 = bytes([1, 1, 1, 1, 1, 1, 8, 16, 24, 32, 110, 251])
    DBS.reset(pckt3)
    assert DBS.field3 == 0x10101
    assert DBS.payload == 0xfb6e20181008
    assert len(DBS) == len(pckt3)

def test_io_mem_wr():
    addr = 0x0
    data = 0xDEADBEEF
    packet = CxlIoMemWrPacket.create(addr, 4, data=data)
    assert packet.data == data
