#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
materialise.py —— 从 CI secrets 生成真实工程文件。

设计原则：本仓库不含任何项目专属信息；一切由 GitHub Secrets 在构建时注入。
安全：只打印「存在 + 规格」，绝不回显 secret 内容到日志。

环境变量：
  TWEAK_SRC_B64   插件源码（base64），含 @@CONFIG_HEADER@@ 等占位符
  BUNDLE_IDS      逗号分隔的 bundle id（写入 filter plist）
  TARGET_PROCS    逗号分隔的目标进程名（写入维护脚本）
  PKG_ID          包 ID 前缀（实际包 ID = <PKG_ID>.<scheme>）
  PKG_NAME / PKG_VERSION / PKG_AUTHOR / PKG_DESC
  TWEAK_NAME      插件实例名（决定 dylib 文件名）
  UL_PATCHES      补丁表 JSON
  UL_IMAGE_BASE / UL_TEXT_SPAN / UL_LOG_PATH / UL_BIN_NAME
"""
import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def need(name):
    v = os.environ.get(name, '')
    if not v:
        print('!! 缺少必需 secret: %s' % name, file=sys.stderr)
        sys.exit(1)
    return v


def spec(name):
    v = os.environ.get(name, '')
    print('   %-20s %s' % (name, '已设置 (%d 字符)' % len(v) if v else '(未设置)'))
    return v


def main():
    print('== 必需 secrets 检查 ==')
    for s in ('TWEAK_SRC_B64', 'BUNDLE_IDS', 'PKG_ID', 'PKG_NAME',
              'PKG_VERSION', 'TWEAK_NAME', 'UL_PATCHES'):
        need(s)
    for s in ('TWEAK_SRC_B64', 'BUNDLE_IDS', 'TARGET_PROCS', 'PKG_ID', 'PKG_NAME',
              'PKG_VERSION', 'PKG_AUTHOR', 'PKG_DESC', 'TWEAK_NAME',
              'UL_PATCHES', 'UL_IMAGE_BASE', 'UL_TEXT_SPAN', 'UL_LOG_PATH',
              'UL_BIN_NAME'):
        spec(s)

    # ---------------------------------------------------------- 1. 补丁表头文件
    print()
    print('== 1) 生成补丁表头文件 ==')
    try:
        patches = json.loads(os.environ['UL_PATCHES'])
    except Exception as e:
        print('!! UL_PATCHES 不是合法 JSON: %s' % e, file=sys.stderr)
        return 1
    if not isinstance(patches, list) or not patches:
        print('!! UL_PATCHES 必须是非空数组', file=sys.stderr)
        return 1
    for i, p in enumerate(patches):
        for k in ('vaddr', 'want', 'new'):
            if k not in p:
                print('!! UL_PATCHES[%d] 缺少 %s' % (i, k), file=sys.stderr)
                return 1
        if len(p['want']) != 8 or len(p['new']) != 8:
            print('!! UL_PATCHES[%d] want/new 必须是 8 个十六进制字符' % i,
                  file=sys.stderr)
            return 1
        int(p['vaddr'], 16)

    hdr = [
        '// 构建期生成 —— 勿手改、勿提交',
        '#ifndef UL_CONFIG_H',
        '#define UL_CONFIG_H',
        '',
        'typedef struct { unsigned long long vaddr; unsigned int expect; '
        'unsigned int patch; } ul_patch_t;',
        '',
        '#define UL_BIN_NAME   "%s"' % os.environ.get('UL_BIN_NAME', ''),
        '#define UL_IMAGE_BASE 0x%sULL' % os.environ.get('UL_IMAGE_BASE', '100000000'),
        '#define UL_TEXT_SPAN  0x%s' % os.environ.get('UL_TEXT_SPAN', '600000'),
        '#define UL_LOG_PATH   "%s"' % os.environ.get('UL_LOG_PATH', '/tmp/tweak.log'),
        '#define UL_PATCH_COUNT %d' % len(patches),
        '',
        'static const ul_patch_t UL_PATCHES[] = {',
    ]
    for p in patches:
        hdr.append('    { 0x%sULL, 0x%su, 0x%su },   /* %s */'
                   % (p['vaddr'], p['want'], p['new'], p.get('desc', '')))
    hdr += ['};', '', '#endif', '']
    open(os.path.join(ROOT, 'ul_config.h'), 'w').write('\n'.join(hdr))
    print('   ul_config.h  %d 字节，补丁 %d 条'
          % (os.path.getsize(os.path.join(ROOT, 'ul_config.h')), len(patches)))

    # ---------------------------------------------------------- 2. 插件源码
    print()
    print('== 2) 释放插件源码 ==')
    raw = base64.b64decode(os.environ['TWEAK_SRC_B64'].strip())
    src = raw.decode('utf-8')
    src = src.replace('@@CONFIG_HEADER@@', '#include "ul_config.h"')
    if '@@' in src:
        left = [l for l in src.split('\n') if '@@' in l]
        print('!! 源码仍有未替换占位符: %s' % left[:3], file=sys.stderr)
        return 1
    open(os.path.join(ROOT, 'Tweak.x'), 'w', encoding='utf-8').write(src)
    print('   Tweak.x  %d 字节' % os.path.getsize(os.path.join(ROOT, 'Tweak.x')))

    # ---------------------------------------------------------- 3. filter plist
    print()
    print('== 3) 生成注入 filter plist ==')
    ids = [x.strip() for x in os.environ['BUNDLE_IDS'].split(',') if x.strip()]
    if not ids:
        print('!! BUNDLE_IDS 为空', file=sys.stderr)
        return 1
    entries = '\n'.join('\t\t\t<string>%s</string>' % b for b in ids)
    plist = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
             '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
             '<plist version="1.0">\n<dict>\n\t<key>Filter</key>\n\t<dict>\n'
             '\t\t<key>Bundles</key>\n\t\t<array>\n'
             + entries + '\n'
             '\t\t</array>\n\t</dict>\n</dict>\n</plist>\n')
    tweak_name = os.environ['TWEAK_NAME']
    # Theos staging 从项目根目录找与 TWEAK_NAME 同名的 plist
    open(os.path.join(ROOT, tweak_name + '.plist'), 'w').write(plist)
    layout_dir = os.path.join(ROOT, 'layout', 'Library', 'MobileSubstrate',
                              'DynamicLibraries')
    os.makedirs(layout_dir, exist_ok=True)
    open(os.path.join(layout_dir, tweak_name + '.plist'), 'w').write(plist)
    print('   %s.plist  注入 %d 个 bundle id' % (tweak_name, len(ids)))

    # ---------------------------------------------------------- 4. Makefile / 维护脚本
    print()
    print('== 4) 渲染 Makefile ==')
    procs = ','.join(x.strip() for x in os.environ.get('TARGET_PROCS', '').split(',')
                     if x.strip())
    mk = open(os.path.join(ROOT, 'Makefile'), encoding='utf-8').read()
    if '@@TARGET_PROCS@@' in mk:
        if procs:
            mk = mk.replace('@@TARGET_PROCS@@', procs)
        else:
            mk = '\n'.join(l for l in mk.split('\n')
                           if '@@TARGET_PROCS@@' not in l)
    open(os.path.join(ROOT, 'Makefile'), 'w', encoding='utf-8').write(mk)
    print('   TARGET_PROCS = %s' % (procs or '(未设置)'))

    print()
    print('== 5) 生成 dpkg 维护脚本 ==')
    sys.path.insert(0, os.path.join(ROOT, 'ci'))
    import importlib.util
    sp = importlib.util.spec_from_file_location(
        'gm', os.path.join(ROOT, 'ci', 'gen_maint.py'))
    gm = importlib.util.module_from_spec(sp)
    gm.__name__ = 'gm'
    sp.loader.exec_module(gm)
    # gen_maint 用 __main__ 保护，这里直接复用它内部逻辑
    body = gm.COMMON.replace('@@PROCS@@', procs.replace(',', ' ').strip())
    ctl = os.path.join(ROOT, 'layout', 'DEBIAN')
    os.makedirs(ctl, exist_ok=True)
    for stage in gm.STAGES:
        path = os.path.join(ctl, stage)
        open(path, 'w').write(body + gm.TAIL.replace('@@STAGE@@', stage))
        os.chmod(path, 0o755)
        print('   %-10s %6d B' % (stage, os.path.getsize(path)))

    print()
    print('== 完成 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
