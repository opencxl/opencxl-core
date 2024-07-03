"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""


def get_pretty_string(obj, pretty_string="", indent=0):
    for field in obj._fields_:
        name = field[0]
        val = field[1]
        if hasattr(val, "_fields_"):
            pretty_string += f"{indent * ' '}{name}:\n"
            pretty_string = get_pretty_string(getattr(obj, name), pretty_string, indent + 2)
        else:
            pretty_string += f"{indent * ' '}{name}: 0x{getattr(obj, name):X}\n"
    return pretty_string
