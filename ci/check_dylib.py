#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dylib 交付前静态验收（纯 Python 解析 Mach-O，不依赖 llvm-otool / otool）。

检查项：
  1) 架构为 arm64
  2) LC_ID_DYLIB 的 install name 与预期一致
  3) 入口段齐全：
       __TEXT,__init_offsets         >= 1 项（现代 dyld4）
       __DATA_CONST,__mod_init_func  size == 8（老式 Substrate/ElleKit 扫描器）
  4) 修复流形态：
       不得出现 LC_DYLD_INFO / LC_DYLD_INFO_ONLY
       必须有 LC_DYLD_CHAINED_FIXUPS

用法: check_dylib.py <dylib> [预期 install_name]
"""
import struct, sys

SEGMENT_64          = 0x19
LC_ID_DYLIB         = 0x0d
LC_DYLD_INFO        = 0x22
LC_DYLD_INFO_ONLY   = 0x80000022
LC_DYLD_CHAINED_FIXUPS = 0x80000034
CPU_TYPE_ARM64      = 0x0100000c


def parse(path):
    d = open(path, 'rb').read()
    if d[:4] != b'\xcf\xfa\xed\xfe':
        raise SystemExit('!! 不是 64 位小端 Mach-O: %s' % path)
    magic, cputype, cpusub, ftype, ncmds, sizeofcmds, flags, _ = \
        struct.unpack_from('<IiiIIIII', d, 0)
    off, cmds = 32, []
    for _ in range(ncmds):
        cmd, cs = struct.unpack_from('<II', d, off)
        cmds.append((cmd, cs, off))
        off += cs
    return d, cputype, cpusub, ftype, cmds


def sections(d, cmds):
    out = {}
    for cmd, cs, o in cmds:
        if cmd != SEGMENT_64:
            continue
        seg = d[o + 8:o + 24].split(b'\0')[0].decode()
        nsects = struct.unpack_from('<I', d, o + 64)[0]
        so = o + 72
        for _ in range(nsects):
            sn = d[so:so + 16].split(b'\0')[0].decode()
            sgn = d[so + 16:so + 32].split(b'\0')[0].decode()
            size = struct.unpack_from('<Q', d, so + 40)[0]
            out[(sgn, sn)] = size
            so += 80
    return out


def main():
    path = sys.argv[1]
    expect_name = sys.argv[2] if len(sys.argv) > 2 else None

    d, cputype, cpusub, ftype, cmds = parse(path)
    fails = []

    print('== %s' % path)
    if cputype != CPU_TYPE_ARM64:
        fails.append('架构不是 arm64 (cputype=%#x)' % cputype)
    print('   架构: arm64  subtype=%d  filetype=%d' % (cpusub, ftype))

    # install name
    iname = None
    for cmd, cs, o in cmds:
        if cmd == LC_ID_DYLIB:
            no = struct.unpack_from('<I', d, o + 8)[0]
            iname = d[o + no:o + cs].split(b'\0')[0].decode()
    print('   install_name: %s' % iname)
    if expect_name and iname != expect_name:
        fails.append('install_name 不符: %s != %s' % (iname, expect_name))

    # 入口段
    secs = sections(d, cmds)
    io = secs.get(('__TEXT', '__init_offsets'), 0)
    mi = secs.get(('__DATA_CONST', '__mod_init_func'), 0)
    mib = secs.get(('__DATA', '__mod_init_func'), 0)
    print('   __init_offsets            %d B' % io)
    print('   __DATA_CONST/__mod_init_func %d B' % mi)
    if mib:
        print('   （注意：__DATA/__mod_init_func %d B —— ld64.lld 不会正确产出，应改 __DATA_CONST）' % mib)
    if io < 4:
        fails.append('缺 __TEXT,__init_offsets（现代 dyld4 不会调用构造器）')
    if mi != 8:
        fails.append('缺 __DATA_CONST,__mod_init_func（老式扫描器不会调用，需 size==8）')

    # 修复流形态
    has_dyldinfo = any(c[0] in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY) for c in cmds)
    has_chained = any(c[0] == LC_DYLD_CHAINED_FIXUPS for c in cmds)
    print('   LC_DYLD_INFO=%s  LC_DYLD_CHAINED_FIXUPS=%s'
          % ('有' if has_dyldinfo else '无', '有' if has_chained else '无'))
    if has_dyldinfo:
        fails.append('出现 LC_DYLD_INFO —— dyld4 可能 halt (bad bind opcode)')
    if not has_chained:
        fails.append('缺 LC_DYLD_CHAINED_FIXUPS')

    if fails:
        for f in fails:
            print('   !! %s' % f)
        print('   => 验收不通过')
        return 1
    print('   => 验收通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
