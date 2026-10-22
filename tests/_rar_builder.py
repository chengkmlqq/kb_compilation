"""最小 RAR4 构造器（仅测试用）。

环境无 rar 压缩工具（rar 只在 Windows 商业版提供，libarchive 的 bsdtar
只支持**读** rar、不支持写）。为让「rar 压缩包自动解包」有真实夹具可测，
这里按 RAR 4.x 规范手工拼字节：Marker + MAIN_HEAD + FILE_HEAD(store 模式)
+ ENDARC。store（method=0x30）= 不压缩直接存原文，因此不需要 zlib。

仅覆盖测试所需的极小子集：ASCII 文件名、stored、无加密/无分卷。
"""

from __future__ import annotations

import struct
import zlib

MARKER = b"Rar!\x1a\x07\x00"


def _crc16(payload: bytes) -> int:
    """RAR 头 CRC：CRC32 的低 16 位。"""
    return zlib.crc32(payload) & 0xFFFF


def _main_head() -> bytes:
    # HEAD_TYPE=0x73, HEAD_FLAGS=0, HEAD_SIZE=13, RESERVED1=0, RESERVED2=0
    body = struct.pack("<BHHHI", 0x73, 0x0000, 13, 0, 0)
    return struct.pack("<H", _crc16(body)) + body


def _end_arc() -> bytes:
    body = struct.pack("<BHH", 0x7B, 0x0000, 7)
    return struct.pack("<H", _crc16(body)) + body


def _file_head(name: str, data: bytes) -> bytes:
    name_bytes = name.encode("utf-8")
    # HEAD_TYPE=0x74 / FLAGS=0x8000(沿用前一个文件的字典位，store 场景无关)
    # HEAD_SIZE = 32 + name_len（实测：libarchive/bsdtar 仅对 HEAD_SIZE>=32 的
    # 构造可稳定解析；<=30 报 "Invalid header size"。含 zip 二进制内容的条目
    # 会让 bsdtar 误读内部字节——夹具仅支持纯文本条目的 rar）
    head_size = 32 + len(name_bytes)
    # PACK_SIZE / UNP_SIZE / HOST_OS(3=Unix) / FILE_CRC / FTIME / UNP_VER(20=RAR2.0)
    # / METHOD(0x30=store) / NAME_SIZE / ATTR
    body = struct.pack(
        "<BHHIIBIIBBHI",
        0x74,          # HEAD_TYPE
        0x0000,        # HEAD_FLAGS
        head_size,     # HEAD_SIZE
        len(data),     # PACK_SIZE
        len(data),     # UNP_SIZE
        3,             # HOST_OS: Unix
        zlib.crc32(data) & 0xFFFFFFFF,  # FILE_CRC
        0x00000000,    # FTIME（不使用时间戳校验）
        20,            # UNP_VER: RAR 2.0
        0x30,          # METHOD: store（不压缩）
        len(name_bytes),
        0o644 << 16,   # ATTR
    ) + name_bytes
    return struct.pack("<H", _crc16(body)) + body


def build_rar(entries: dict[str, bytes]) -> bytes:
    """按 {包内路径: 内容} 生成 RAR4 字节流（store 模式）。"""
    out = [MARKER, _main_head()]
    for name, data in entries.items():
        out.append(_file_head(name, data))
        out.append(data)
    out.append(_end_arc())
    return b"".join(out)