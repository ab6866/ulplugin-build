#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
按 package scheme 渲染 control。

为什么需要：两个 scheme 必须用**不同的 Package ID** 并互相 Conflicts/Replaces，
否则同时安装会被 dpkg 视为同一包的不同架构版本（既不互斥也会互相覆盖）。
Architecture 交给 Theos 按 scheme 自行设置，这里不写。

用法： PKG_ID=com.x.y SCHEME=roothide python3 ci/render_control.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    pkg_id = os.environ.get('PKG_ID', '').strip()
    scheme = os.environ.get('SCHEME', '').strip()
    if not pkg_id or not scheme:
        print('!! 需要 PKG_ID 与 SCHEME', file=sys.stderr)
        return 1
    if scheme not in ('roothide', 'rootless'):
        print('!! SCHEME 必须是 roothide 或 rootless', file=sys.stderr)
        return 1

    other = 'rootless' if scheme == 'roothide' else 'roothide'
    conflict = '%s.%s' % (pkg_id, other)

    src = open(os.path.join(ROOT, 'control.in'), encoding='utf-8').read()
    subs = {
        '@@PKG_ID@@': '%s.%s' % (pkg_id, scheme),
        '@@PKG_CONFLICT@@': conflict,
        '@@PKG_NAME@@': os.environ.get('PKG_NAME', ''),
        '@@PKG_VERSION@@': os.environ.get('PKG_VERSION', ''),
        '@@PKG_AUTHOR@@': os.environ.get('PKG_AUTHOR', ''),
        '@@PKG_DESC@@': os.environ.get('PKG_DESC', ''),
    }
    for k, v in subs.items():
        src = src.replace(k, v)

    # 去掉 Architecture 行，交给 Theos 按 scheme 决定
    src = '\n'.join(l for l in src.split('\n') if not l.startswith('Architecture:'))

    leftover = src.count('@@')
    if leftover:
        print('!! control 仍有 %d 处未替换占位符' % leftover, file=sys.stderr)
        return 1

    out = os.path.join(ROOT, 'control')
    open(out, 'w', encoding='utf-8').write(src)
    print('control 已渲染: scheme=%s package=%s.%s conflicts=%s'
          % (scheme, pkg_id, scheme, conflict))
    print('---')
    print(src)


if __name__ == '__main__':
    sys.exit(main())
