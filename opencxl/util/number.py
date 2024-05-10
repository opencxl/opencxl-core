"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import sys
import os
import random


def round_up_to_power_of_2(number: int) -> int:
    # Subtract 1 from the number
    number -= 1

    # Perform bitwise OR with the number and its right-shifted value
    number |= number >> 1
    number |= number >> 2
    number |= number >> 4
    number |= number >> 8
    number |= number >> 16

    # Add 1 to the result to get the next power of 2
    number += 1

    return number


def get_randbits(n_bits: int):
    # truly random via /dev/random
    s = os.urandom(100)
    random.seed(s)
    return random.getrandbits(n_bits)


def bswap16(n):
    return ((n & 0xFF00) >> 8) | ((n & 0x00FF) << 8)


def bswap32(n):
    return (
        ((n & 0xFF000000) >> 24)
        | ((n & 0x00FF0000) >> 8)
        | ((n & 0x0000FF00) << 8)
        | ((n & 0x000000FF) << 24)
    )


def bswap64(n):
    return (
        ((n & 0xFF00000000000000) >> 56)
        | ((n & 0x00FF000000000000) >> 40)
        | ((n & 0x0000FF0000000000) >> 24)
        | ((n & 0x000000FF00000000) >> 8)
        | ((n & 0x00000000FF000000) << 8)
        | ((n & 0x0000000000FF0000) << 24)
        | ((n & 0x000000000000FF00) << 40)
        | ((n & 0x00000000000000FF) << 56)
    )


def to_be16(n):
    if sys.byteorder == "big":
        return n
    return bswap16(n)


def to_be32(n):
    if sys.byteorder == "big":
        return n
    return bswap32(n)


def to_be64(n):
    if sys.byteorder == "big":
        return n
    return bswap64(n)


def htotlp16(n):
    return to_be16(n)


def htotlp32(n):
    return to_be32(n)


def htotlp64(n):
    return to_be64(n)


def tlptoh16(n):
    if sys.byteorder == "big":
        return n
    return bswap16(n)


def tlptoh32(n):
    if sys.byteorder == "big":
        return n
    return bswap32(n)


def tlptoh64(n):
    if sys.byteorder == "big":
        return n
    return bswap64(n)
