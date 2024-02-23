"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""
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
