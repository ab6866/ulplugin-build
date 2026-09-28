#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验 postrm 的还原结果（语义级）：

  1) 每个补丁点必须逐字节回到原始值 —— 硬性条件
  2) 其余差异只允许落在 __LINKEDIT 尺寸与 LC_CODE_SIGNATURE 尺寸上
     （ldid 重签必然改变签名长度，属正常，不是还原失败）

用法: verify_restore.py <还原后二进制> <原始二进制> <spec.json>
"""
import struct, sys, json


def load_cmds(path):
    d = open(path, 'rb').read()
    ncmds = struct.unpack_from('<I', d, 16)[0]
    off, out = 32, []
    for _ in range(ncmds):
        cmd, cs = struct.unpack_from('<II', d, off)
        out.append((cmd, cs, off))
        off += cs
    return out, d


def sig_dataoff(path):
    cmds, d = load_cmds(path)
    for cmd, cs, off in cmds:
        if cmd == 0x1d:  # LC_CODE_SIGNATURE
            return struct.unpack_from('<II', d, off + 8)[0]
    return len(d)


def main():
    if len(sys.argv) < 4:
        print('usage: verify_restore.py <restored> <orig> <spec.json>')
        return 1
    rest, orig, specp = sys.argv[1], sys.argv[2], sys.argv[3]

    cmds, b = load_cmds(orig)
    a = open(rest, 'rb').read()

    n = min(sig_dataoff(orig), sig_dataoff(rest), len(a), len(b))

    # 允许变化的区域：__LINKEDIT 段尺寸字段、LC_CODE_SIGNATURE 尺寸字段
    allowed = set()
    for cmd, cs, off in cmds:
        if cmd == 0x19:                                # LC_SEGMENT_64
            segname = b[off + 8:off + 24].split(b'\0')[0].decode()
            if segname == '__LINKEDIT':
                # vmsize(off+32..40) fileoff(40..48) filesize(48..56)
                # 签名长度变化会连带 filesize 变化，属正常
                for i in range(off + 32, off + 56):
                    allowed.add(i)
        if cmd == 0x1d:                                # LC_CODE_SIGNATURE
            for i in range(off + 8, off + 16):         # dataoff/datasize
                allowed.add(i)

    diff = [i for i in range(n) if a[i] != b[i]]
    unexpected = [i for i in diff if i not in allowed]
    print('  差异字节共 %d，其中允许区（签名/段尺寸）%d，意外 %d'
          % (len(diff), len(diff) - len(unexpected), len(unexpected)))
    for i in unexpected[:10]:
        print('    !! @0x%x orig=%02x now=%02x' % (i, b[i], a[i]))

    spec = json.load(open(specp))
    base = int(spec['image_base'], 16)
    ok = 0
    for p in spec['patches']:
        off = int(p['vaddr'], 16) - base
        if a[off:off + 4].hex() == p['want']:
            ok += 1
        else:
            print('    !! 补丁点 %#x 未还原: %s (期望 %s)'
                  % (off, a[off:off + 4].hex(), p['want']))
    print('  补丁点还原 %d / %d' % (ok, len(spec['patches'])))

    if ok == len(spec['patches']) and not unexpected:
        print('  ★ 还原成功（代码语义与原始一致）')
        return 0
    print('  !! 还原校验未通过')
    return 1


if __name__ == '__main__':
    sys.exit(main())
