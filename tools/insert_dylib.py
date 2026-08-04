#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯 Python 版 insert_dylib —— 向 Mach-O 主程序追加一条 LC_LOAD_DYLIB，
使目标 dylib 在启动时被 dyld 自动加载。

支持：thin(arm64) 与 fat 二进制。会在每个 arch 的 header 尾部空闲区插入
load command，并更新 ncmds / sizeofcmds。

用法:
    python insert_dylib.py <dylib路径(写进LC的字符串)> <目标Mach-O> [输出路径]

示例:
    python insert_dylib.py "@executable_path/KeepAlive.dylib" Stocks Stocks
"""
import sys, struct, shutil, os

MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC   = 0xCAFEBABE
FAT_CIGAM   = 0xBEBAFEBE
LC_LOAD_DYLIB = 0x0C
LC_REQ_DYLD   = 0x80000000

def align(v, a):
    return (v + a - 1) & ~(a - 1)

def build_dylib_lc(path_str):
    """构造一条 LC_LOAD_DYLIB command 字节。"""
    name = path_str.encode('utf-8') + b'\x00'
    # dylib_command: cmd(4) cmdsize(4) name.offset(4) timestamp(4)
    #                current_version(4) compatibility_version(4) + name
    name_offset = 24
    cmdsize = align(name_offset + len(name), 8)
    lc = struct.pack('<IIIIII',
                     LC_LOAD_DYLIB,
                     cmdsize,
                     name_offset,
                     2,          # timestamp
                     0x10000,    # current_version 1.0.0
                     0x10000)    # compatibility_version 1.0.0
    lc += name
    lc += b'\x00' * (cmdsize - len(lc))
    return lc

LC_CODE_SIGNATURE = 0x1D

def strip_code_signature(data):
    """移除 thin arm64 Mach-O 的 LC_CODE_SIGNATURE 并截断签名数据。
    Apple codesign 会因残留旧签名而报 internal error；剥离后交给 ldid 重签。
    仅处理单一 thin 64-bit（本项目主程序即是）。返回新的 bytearray。"""
    magic = struct.unpack_from('<I', data, 0)[0]
    if magic != MH_MAGIC_64:
        return data  # 非 thin64，跳过
    ncmds = struct.unpack_from('<I', data, 16)[0]
    sizeofcmds = struct.unpack_from('<I', data, 20)[0]
    off = 32
    cs_off = None; cs_size = None; lc_pos = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == LC_CODE_SIGNATURE:
            cs_off = off
            cs_size = cmdsize
            # linkedit_data_command: cmd cmdsize dataoff datasize
            sig_dataoff = struct.unpack_from('<I', data, off+8)[0]
            sig_datasize = struct.unpack_from('<I', data, off+12)[0]
            lc_pos = (sig_dataoff, sig_datasize)
        off += cmdsize
    if cs_off is None:
        print('  无 LC_CODE_SIGNATURE，跳过剥离')
        return data
    # 1) 从 load commands 区删除这条 LC，后续 LC 前移，尾部补零保持区大小
    lc_region_start = 32
    lc_region_end = 32 + sizeofcmds
    before = data[lc_region_start:cs_off]
    after = data[cs_off+cs_size:lc_region_end]
    newregion = before + after + b'\x00' * cs_size
    data[lc_region_start:lc_region_end] = newregion
    struct.pack_into('<I', data, 16, ncmds - 1)
    struct.pack_into('<I', data, 20, sizeofcmds - cs_size)
    # 2) 截断文件末尾的签名数据
    sig_dataoff, sig_datasize = lc_pos
    if sig_dataoff and sig_dataoff <= len(data):
        data = data[:sig_dataoff]
    print('  已剥离 LC_CODE_SIGNATURE (签名数据 %d 字节已截断)' % sig_datasize)
    return data

def process_thin(data, offset, path_str):
    """在单个(thin)Mach-O 头处插入 load command。返回修改后的 data。"""
    magic = struct.unpack_from('<I', data, offset)[0]
    if magic != MH_MAGIC_64:
        raise RuntimeError('仅支持 64-bit little-endian Mach-O, magic=%x' % magic)

    # mach_header_64: magic cputype cpusubtype filetype ncmds sizeofcmds flags reserved
    ncmds     = struct.unpack_from('<I', data, offset + 16)[0]
    sizeofcmds = struct.unpack_from('<I', data, offset + 20)[0]

    header_size = 32
    lc_start = offset + header_size
    lc_end   = lc_start + sizeofcmds

    new_lc = build_dylib_lc(path_str)

    # 检查 load command 区之后、第一个 section 数据之前是否有足够空闲空间。
    # 找出所有 segment 里最小的文件偏移作为可用上限。
    first_seg_fileoff = None
    off = lc_start
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x19:  # LC_SEGMENT_64
            segname = data[off+8:off+24].split(b'\x00')[0]
            fileoff = struct.unpack_from('<Q', data, off+32)[0]
            filesize = struct.unpack_from('<Q', data, off+40)[0]
            # __TEXT segment fileoff 通常是 0，跳过它自身；我们关心紧跟 header 的数据
            if filesize > 0:
                if first_seg_fileoff is None or (fileoff > 0 and fileoff < first_seg_fileoff):
                    if fileoff > 0:
                        first_seg_fileoff = fileoff
        off += cmdsize

    # 头部 load command 结束位置到第一段数据的空隙
    # __TEXT 段一般从文件 0 开始且包含 header，真正可写的空隙 = 第一个 section 的 offset
    # 用最保守方式：找 __TEXT 段里第一个 section 的 offset
    gap_limit = None
    off = lc_start
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x19:
            segname = data[off+8:off+24].split(b'\x00')[0]
            nsects = struct.unpack_from('<I', data, off+64)[0]
            so = off + 72
            for _ in range(nsects):
                secoff = struct.unpack_from('<I', data, so+48)[0]
                if secoff > 0:
                    if gap_limit is None or secoff < gap_limit:
                        gap_limit = secoff
                so += 80
        off += cmdsize

    if gap_limit is None:
        gap_limit = lc_end  # 兜底

    free_space = gap_limit - (lc_end - offset)
    if free_space < len(new_lc):
        raise RuntimeError('header 空闲空间不足: 需要 %d, 可用 %d。'
                           '该二进制无法用追加方式注入。'
                           % (len(new_lc), free_space))

    # 写入新的 load command
    data[lc_end:lc_end+len(new_lc)] = new_lc
    # 更新 ncmds / sizeofcmds
    struct.pack_into('<I', data, offset + 16, ncmds + 1)
    struct.pack_into('<I', data, offset + 20, sizeofcmds + len(new_lc))
    print('  [thin @0x%x] 已插入 LC_LOAD_DYLIB (cmdsize=%d), 空闲余量 %d 字节'
          % (offset, len(new_lc), free_space - len(new_lc)))
    return data

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    dylib_path = sys.argv[1]
    target = sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else target

    with open(target, 'rb') as f:
        data = bytearray(f.read())

    magic = struct.unpack_from('>I', data, 0)[0]
    if magic in (FAT_MAGIC, FAT_CIGAM):
        nfat = struct.unpack_from('>I', data, 4)[0]
        print('FAT 二进制, %d 个架构' % nfat)
        for i in range(nfat):
            # fat_arch: cputype cpusubtype offset size align
            ao = 8 + i * 20
            arch_off = struct.unpack_from('>I', data, ao + 8)[0]
            data = process_thin(data, arch_off, dylib_path)
    else:
        magic_le = struct.unpack_from('<I', data, 0)[0]
        if magic_le in (MH_MAGIC_64,):
            print('Thin 64-bit Mach-O')
            # 先剥离旧签名，避免后续 ldid/codesign 因结构不自洽报错
            data = strip_code_signature(data)
            data = process_thin(data, 0, dylib_path)
        else:
            raise RuntimeError('无法识别的文件格式 magic=%x' % magic_le)

    with open(out, 'wb') as f:
        f.write(data)
    print('完成 -> %s' % out)

if __name__ == '__main__':
    main()
