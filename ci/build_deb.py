#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建双方案 DEB（rootless / roothide）
产出 Theos 原生的 ar 归档（不重封），control.tar 内成员名为 ./control ./postinst ./postrm。

data.tar 内容（全部落在越狱环境已存在的目录，无需自建目录条目，
否则 dpkg 会因父目录不存在而 unpack 失败）：
  <payload_root>/ULPlugin.dylib      运行期改码插件
  <payload_root>/ULPlugin.plist      Substrate 过滤（绑定 bundle）
"""
import os, io, gzip, tarfile, hashlib, json, sys, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'dist')
TW = os.path.join(ROOT, 'tweak')
GEN = os.path.join(TW, 'generated')
DSC = os.path.join(ROOT, 'dsc')
DYLIB_SUFFIX = os.environ.get('UL_DYLIB_SUFFIX', '')

CONTROL = """Package: {pkg}
Name: {name}
Version: {ver}
Architecture: {arch}
Description: {desc}
Author: 6866
Maintainer: 6866
Section: Tweaks
Depends: firmware (>= 15.0)
{extra}"""


def targz(entries, mtime=1700000000):
    """entries: list of (arcname, data, mode)
    arcname 以 '/' 结尾 -> 目录条目（必须提供，否则 dpkg 无法创建父目录）
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for name, data, mode in entries:
            ti = tarfile.TarInfo(name)
            ti.mode = mode
            ti.mtime = mtime
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            if name.endswith('/'):
                ti.type = tarfile.DIRTYPE
                ti.size = 0
                tar.addfile(ti)
            else:
                ti.type = tarfile.REGTYPE
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode='wb', mtime=mtime) as g:
        g.write(buf.getvalue())
    return gz.getvalue()


def dir_entries(path):
    """'a/b/c/file' -> ['a/', 'a/b/', 'a/b/c/']（含所有中间层）"""
    parts = path.strip('/').split('/')[:-1]
    out, cur = [], ''
    for x in parts:
        cur = cur + '/' + x if cur else x
        out.append(cur + '/')
    return out


def ar(members):
    """Debian ar：成员名以 '/' 结尾；奇数长度补 '\\n'"""
    out = b'!<arch>\n'
    for name, data in members:
        hdr = ((name + '/')[:16].encode().ljust(16) + b'0'.ljust(12) +
               b'0'.ljust(6) + b'0'.ljust(6) + b'100644'.ljust(8) +
               str(len(data)).encode().ljust(10) + b'\x60\n')
        out += hdr + data + (b'\n' if len(data) % 2 else b'')
    return out


SAME = 'Conflicts: %s\nReplaces: %s\nProvides: com.6866.ulplugin\n'
RL = 'com.6866.ulplugin.rootless'
RH = 'com.6866.ulplugin.roothide'


def make(scheme, pkg, name, arch, desc, extra, payload_root, dylib, patches):
    # ---- 维护脚本 ----
    spec = json.load(open(os.path.join(ROOT, 'spec.json')))
    spec['scheme'] = scheme
    gen = os.path.join(ROOT, 'ci', 'gen_maintain.py')
    sys.path.insert(0, os.path.join(ROOT, 'ci'))
    import importlib.util
    m = importlib.util.spec_from_file_location('gm_%s' % scheme, gen)
    mod = importlib.util.module_from_spec(m)
    m.loader.exec_module(mod)
    mod.build(spec, DSC)

    control = CONTROL.format(pkg=pkg, name=name, ver=spec['version'], arch=arch,
                             desc=desc, extra=extra)
    ctl = targz([
        ('./control', control.encode(), 0o644),
        ('./postinst', open(os.path.join(DSC, 'postinst.%s' % scheme), 'rb').read(), 0o755),
        ('./postrm', open(os.path.join(DSC, 'postrm.%s' % scheme), 'rb').read(), 0o755),
    ])
    files = [
        (payload_root.lstrip('/') + '/ULPlugin.dylib', open(dylib, 'rb').read(), 0o755),
        (payload_root.lstrip('/') + '/ULPlugin.plist',
         open(os.path.join(GEN, 'ULPlugin.plist'), 'rb').read(), 0o644),
    ]
    # 补上所有父目录条目（否则设备上目录不存在时 dpkg unpack 直接失败）
    dirs, seen = [], set()
    dirs.append(('./', b'', 0o755))
    for fname, _d, _m in files:
        for d in dir_entries(fname):
            if d not in seen:
                seen.add(d)
                dirs.append((d, b'', 0o755))
    data = targz(dirs + files)

    deb = ar([('debian-binary', b'2.0\n'),
              ('control.tar.gz', ctl), ('data.tar.gz', data)])
    path = os.path.join(OUT, '%s.deb' % name.replace(' ', '-').lower())
    open(path, 'wb').write(deb)
    print('%-34s %7d B  sha256=%s' % (os.path.basename(path), len(deb),
                                      hashlib.sha256(deb).hexdigest()))
    return path


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    spec = json.load(open(os.path.join(ROOT, 'spec.json')))
    patches = spec['patches']
    make('rootless', RL, 'ULPlugin-rootless', 'iphoneos-arm64',
         'Runtime modification utility - rootless',
         SAME % (RH, RH), '/var/jb/Library/MobileSubstrate/DynamicLibraries',
         os.path.join(TW, 'ULPlugin_rootless'+DYLIB_SUFFIX+'.dylib'), patches)
    make('roothide', RH, 'ULPlugin-roothide', 'iphoneos-arm64e',
         'Runtime modification utility - roothide',
         SAME % (RL, RL), '/Library/MobileSubstrate/DynamicLibraries',
         os.path.join(TW, 'ULPlugin_roothide'+DYLIB_SUFFIX+'.dylib'), patches)
