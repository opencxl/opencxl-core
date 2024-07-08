from sortedcontainers import SortedDict
from ctypes import Structure, POINTER, c_uint8, byref, pointer, addressof, cast, memmove, memset
from typing import Iterable

PAGE_SZ = 16
SENTINEL = 0xFE

def round_down_to_page_boundary(addr: int) -> int:
    return (addr // PAGE_SZ) * PAGE_SZ

def next_page(page_addr: int) -> int:
    return page_addr + PAGE_SZ

def cast_mv_to_ptr(mv: memoryview, off: int):
    return byref(c_uint8.from_buffer(mv.obj), off)

def iterate_over_pages(s: int, e: int) -> Iterable:
    """
    s and e must be page-aligned.
    Inclusive.
    """
    return range(s, e + PAGE_SZ, PAGE_SZ)

class Page(Structure):
    _fields_ = [
        ("_data", c_uint8 * PAGE_SZ)
    ]

    def __init__(self):
        super().__init__()
        memset(self._data, SENTINEL, PAGE_SZ)

class Simple64BitEmulator:
    _memory: SortedDict

    def __init__(self):
        self._memory = SortedDict()

    def read(self, addr: int, buf: memoryview):
        """
        Fills buf with contents of memory from addr to addr + len(buf).
        """
        count = len(buf)
        pg_lower = round_down_to_page_boundary(addr)
        pg_upper = round_down_to_page_boundary(addr + count)
        pg_addr_range = iterate_over_pages(pg_lower, pg_upper)

        offset_within_page = addr % PAGE_SZ
        bytes_read = 0
        curr_addr = addr

        for page_idx, page_addr in enumerate(pg_addr_range):
            # compute how much of the buffer to fill
            next_pg = next_page(page_addr)
            # below, note that we must subtract curr_addr in case addr is not page-aligned
            buf_slice = buf[bytes_read : min(len(buf), bytes_read + next_pg - curr_addr)]
            len_slice = len(buf_slice)
            buf_ptr = cast_mv_to_ptr(buf, bytes_read)

            if page_addr not in self._memory:
                # if page_addr is not in self._memory, that means we haven't written to it,
                # so we can just fill the buffer with SENTINEL
                memset(buf_ptr, SENTINEL, len_slice)
            else:
                # otherwise, read whatever's in memory into the buffer slice
                page = self._memory[page_addr]

                # note that if addr is not page aligned and this is the first page,
                # we have to match the page offset
                if page_idx == 0:
                    memmove(buf_ptr, pointer(page._data[offset_within_page]), len_slice)
                else:
                    memmove(buf_ptr, page._data, len_slice)

            # after the first page, the current address we're reading from
            # is unconditionally page-aligned
            curr_addr = next_page(page_addr)

            # update pointer to first unwritten byte within buffer
            bytes_read += len_slice


if __name__ == "__main__":
    emulator = Simple64BitEmulator()
    buf = bytearray(48)
    emulator.read(1, memoryview(buf))
    print(buf)
