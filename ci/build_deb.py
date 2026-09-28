#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建双方案 DEB（标准 / 备用）
产出 Theos 原生的 ar 归档（不重封），control.tar 内成员名为 ./control ./postinst ./postrm。

data.tar 内容：
  <payload_root>/ULPlugin.dylib      运行期改码插件（第二层保险）
  <payload_root>/ULPlugin.plist      Substrate 过滤（绑定 bundle）
  /var/mobile/Library/ULPlugin/patches.json   地址表（postinst 读取，仓库内零特征）
"""
import os, io, gzip, tarfile, hashlib, json, sys, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'dist')
TW = os.path.join(ROOT, 'tweak')
GEN = os.path.join(TW, 'generated')
DSC = os.path.join(ROOT, 'dsc')
DYLIB_SUFFIX = os.environ.get('UL_DYLIB_SUFFIX', '')
CONFIG_DIR = '/var/mobile/Library/ULPlugin'

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
    """entries: list of (arcname, data, mode)"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for name, data, mode in entries:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            ti.mtime = mtime
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            tar.addfile(ti, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode='wb', mtime=mtime) as g:
        g.write(buf.getvalue())
    return gz.getvalue()


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
    data = targz([
        (payload_root + '/ULPlugin.dylib', open(dylib, 'rb').read(), 0o644),
        (payload_root + '/ULPlugin.plist',
         open(os.path.join(GEN, 'ULPlugin.plist'), 'rb').read(), 0o644),
        (CONFIG_DIR + '/patches.json',
         json.dumps(patches, indent=1).encode(), 0o644),
    ])
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
    make('rootless', RL, 'ULPlugin-standard', 'iphoneos-arm64',
         'Runtime modification utility - standard scheme',
         SAME % (RH, RH), '/var/jb/Library/MobileSubstrate/DynamicLibraries',
         os.path.join(TW, 'ULPlugin_rootless'+DYLIB_SUFFIX+'.dylib'), patches)
    make('roothide', RH, 'ULPlugin-alternate', 'iphoneos-arm64e',
         'Runtime modification utility - alternate scheme',
         SAME % (RL, RL), '/Library/MobileSubstrate/DynamicLibraries',
         os.path.join(TW, 'ULPlugin_roothide'+DYLIB_SUFFIX+'.dylib'), patches)
