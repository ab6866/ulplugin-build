#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建期把 Secrets 注入模板，生成可直接编译的 Theos 工程。

产出：
  Tweak.x                                        Logos 源码（含补丁表）
  generated/config.h                             补丁表头文件
  layout/Library/MobileSubstrate/DynamicLibraries/Tweak.plist  注入过滤
  layout/DEBIAN/postinst / postrm                维护脚本

仓库内零真实标识：所有目标特征都来自环境变量 / GitHub Secrets。
"""
import json, os, sys, plistlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, 'generated')
LAYOUT = os.path.join(ROOT, 'layout')


def env(name, default=None, required=True):
    v = os.environ.get(name, default)
    if required and not v:
        print('!! 缺少环境变量 %s' % name)
        sys.exit(1)
    return v


def main(scheme):
    app_name = env('UL_APP_NAME')
    bin_name = env('UL_BIN_NAME')
    bundle_id = env('UL_BUNDLE_ID')
    version = env('VERSION', '1.1.0', required=False)
    target_procs = env('UL_TARGET_PROCS', bin_name, required=False)
    image_base = env('UL_IMAGE_BASE', '100000000', required=False)
    text_span = env('UL_TEXT_SPAN', '600000', required=False)
    log_path = env('UL_LOG_PATH', '/var/mobile/Library/Logs/tweak.log', required=False)
    patches = json.loads(env('UL_PATCHES'))

    for i, p in enumerate(patches):
        for k in ('vaddr', 'want', 'new'):
            if k not in p:
                print('!! UL_PATCHES[%d] 缺少 %s' % (i, k)); sys.exit(1)
        if len(p['want']) != 8 or len(p['new']) != 8:
            print('!! UL_PATCHES[%d] want/new 必须是 8 个十六进制字符' % i); sys.exit(1)
        int(p['vaddr'], 16); int(p['want'], 16); int(p['new'], 16)

    os.makedirs(GEN, exist_ok=True)
    os.makedirs(os.path.join(LAYOUT, 'DEBIAN'), exist_ok=True)
    dylib_dir = os.path.join(LAYOUT, 'Library', 'MobileSubstrate', 'DynamicLibraries')
    os.makedirs(dylib_dir, exist_ok=True)
    os.makedirs(LAYOUT, exist_ok=True)

    # ---- 补丁表头文件 ----
    lines = [
        '// 构建期生成 —— 勿手改、勿提交',
        '#ifndef CONFIG_H',
        '#define CONFIG_H',
        '',
        'typedef struct { unsigned long long vaddr; unsigned int expect; unsigned int patch; } ul_patch_t;',
        '',
        '#define UL_BIN_NAME   "%s"' % bin_name,
        '#define UL_IMAGE_BASE 0x%xULL' % int(image_base, 16),
        '#define UL_TEXT_SPAN  0x%s' % text_span,
        '#define UL_LOG_PATH   "%s"' % log_path,
        '#define UL_PATCH_COUNT %d' % len(patches),
        '',
        'static const ul_patch_t UL_PATCHES[] = {',
    ]
    for p in patches:
        lines.append('    { 0x%sULL, 0x%su, 0x%su },' % (p['vaddr'], p['want'], p['new']))
    lines += ['};', '', '#endif', '']
    open(os.path.join(GEN, 'config.h'), 'w').write('\n'.join(lines))

    # ---- Tweak.x ----
    src = open(os.path.join(ROOT, 'Tweak.x.in'), encoding='utf-8').read()
    src = src.replace('@@CONFIG_HEADER@@', '#include "ticonfig.h"')
    open(os.path.join(ROOT, 'Tweak.x'), 'w', encoding='utf-8').write(src)

    # 头文件放到 Theos 能 include 到的地方
    import shutil
    shutil.copy(os.path.join(GEN, 'config.h'), os.path.join(ROOT, 'ticonfig.h'))
    if not os.path.exists(os.path.join(ROOT, 'ticonfig.h')):
        print('!! ticonfig.h 生成失败'); sys.exit(1)

    # ---- filter plist（顶层必须直接是 Filter）----
    # Theos 在 staging 阶段从**项目根目录**读 Tweak.plist，两种位置都放一份。
    flt = {'Filter': {'Bundles': [bundle_id]}}
    # 用 XML 格式（与真机验证过的包一致），避免二进制 plist 的解析差异
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
           '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
           '<plist version="1.0">\n<dict>\n\t<key>Filter</key>\n\t<dict>\n'
           '\t\t<key>Bundles</key>\n\t\t<array>\n'
           '\t\t\t<string>%s</string>\n'
           '\t\t</array>\n\t</dict>\n</dict>\n</plist>\n' % bundle_id)
    # 只写与 dylib 同名的那一份（Theos staging 会从项目根目录找同名 plist）
    for p in (os.path.join(ROOT, 'ULUnlock.plist'),
              os.path.join(dylib_dir, 'ULUnlock.plist')):
        with open(p, 'w') as f:
            f.write(xml)

    # ---- 维护脚本：结束目标进程，让下次冷启动重新注入 ----
    KILL_FN = """kill_app() {
    f=0
    for c in /var/jb/bin/launchctl /var/jb/usr/bin/launchctl /bin/launchctl /usr/bin/launchctl; do
        [ -x "$c" ] && { "$c" killall "$1" >/dev/null 2>&1 && f=1; break; }
    done
    if [ "$f" = "0" ]; then
        for k in /var/jb/usr/bin/killall /var/jb/bin/killall /usr/bin/killall /bin/killall; do
            [ -x "$k" ] && { "$k" -9 "$1" >/dev/null 2>&1 && f=1; break; }
        done
    fi
    if [ "$f" = "0" ]; then
        for p in /proc/[0-9]*; do
            [ -r "$p/comm" ] || continue
            [ "$(cat "$p/comm" 2>/dev/null)" = "$1" ] || continue
            kill -9 "${p#/proc/}" >/dev/null 2>&1
        done
    fi
}
"""
    procs = [x for x in target_procs.split(',') if x]
    calls = '\n'.join('kill_app %s' % x for x in procs)

    postinst = ('#!/bin/sh\n'
                '# 结束目标进程，使 dylib 在下次冷启动时重新注入\n'
                'trap \'exit 0\' EXIT\n'
                + KILL_FN + calls + '\n'
                'echo "[tweak] 已结束目标进程，重新打开 App 即生效"\n'
                'exit 0\n')
    # postrm 必须自带 kill_app 定义（dpkg 有两个独立脚本，不共享函数）
    postrm = ('#!/bin/sh\n'
              'trap \'exit 0\' EXIT\n'
              + KILL_FN + calls + '\n'
              'echo "[tweak] 卸载完成"\n'
              'exit 0\n')
    for n, body in (('postinst', postinst), ('postrm', postrm)):
        path = os.path.join(LAYOUT, 'DEBIAN', n)
        open(path, 'w').write(body)
        os.chmod(path, 0o755)

    # ---- control（Theos 需要 layout/DEBIAN/control）----
    ctrl = open(os.path.join(ROOT, 'control.in'), encoding='utf-8').read()
    ctrl = ctrl.replace('@@VERSION@@', version)
    pkg = 'com.6866.tweak.%s' % scheme
    other = 'com.6866.tweak.%s' % ('rootless' if scheme == 'roothide' else 'roothide')
    ctrl = ctrl.replace('@@PKG@@', pkg)
    ctrl = ctrl.replace('@@NAME@@', 'Tweak-%s' % scheme)
    # 接管同名 dylib，避免 dpkg 因 "trying to overwrite" 拒绝安装。
    # 注意 dpkg 对重复字段只取最后一个，必须合并而不能追加。
    ctrl = ctrl.replace('@@OTHER@@', other)
    old_repl = 'Replaces: %s' % other
    ctrl = ctrl.replace(old_repl,
        'Replaces: %s, com.6866.subtrackerunlock.roothide, com.6866.subtrackerunlock.rootless' % other)
    ctrl += 'Breaks: com.6866.subtrackerunlock.roothide, com.6866.subtrackerunlock.rootless\n'
    open(os.path.join(LAYOUT, 'DEBIAN', 'control'), 'w').write(ctrl)

    print('materialised:')
    print('  generated/config.h  %d B' % os.path.getsize(os.path.join(GEN, 'config.h')))
    print('  Tweak.x             %d B' % os.path.getsize(os.path.join(ROOT, 'Tweak.x')))
    print('  filter              %s' % bundle_id)
    print('  补丁数              %d' % len(patches))
    print('  目标进程            %s' % target_procs)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'roothide')
