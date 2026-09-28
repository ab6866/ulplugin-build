#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建期生成目标特征（全部取自环境变量 / GitHub Secrets）：
  tweak/generated/ul_config.h   —— 补丁表 + 二进制名 + 镜像基址 + 日志路径
  tweak/generated/ULPlugin.plist —— Substrate 过滤（绑定 bundle）
  spec.json                     —— 供 gen_maintain.py / build_deb.py 使用

仓库内**零真实标识**：generated/ 与 spec.json 均被 .gitignore 忽略。
"""
import json, os, plistlib, hashlib, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GEN = os.path.join(ROOT, 'tweak', 'generated')


def env(name, default=None, required=True):
    v = os.environ.get(name, default)
    if required and not v:
        print('!! 缺少环境变量 %s' % name)
        sys.exit(1)
    return v


def main():
    app_name = env('UL_APP_NAME')                 # 不含 .app
    bin_name = env('UL_BIN_NAME')                 # 主可执行文件名
    bundle_id = env('UL_BUNDLE_ID')               # 过滤用
    version = env('UL_VERSION', '1.0.1', required=False)
    image_base = env('UL_IMAGE_BASE', '100000000', required=False)
    text_span = env('UL_TEXT_SPAN', '600000', required=False)
    log_path = env('UL_LOG_PATH', '/tmp/ulmod.log', required=False)
    boot_cls = env('UL_BOOTSTRAP_CLASS', 'ULBootstrap', required=False)

    raw = env('UL_PATCHES')                       # JSON 数组
    patches = json.loads(raw)
    if not isinstance(patches, list) or not patches:
        print('!! UL_PATCHES 必须是形如 [{"vaddr":"..","want":"..","new":".."}, ...] 的 JSON 数组')
        sys.exit(1)

    # ---- 校验每个补丁 ----
    for i, p in enumerate(patches):
        for k in ('vaddr', 'want', 'new'):
            if k not in p:
                print('!! UL_PATCHES[%d] 缺少 %s' % (i, k)); sys.exit(1)
        if len(p['want']) != 8 or len(p['new']) != 8:
            print('!! UL_PATCHES[%d] want/new 必须各是 8 个十六进制字符' % i); sys.exit(1)
        int(p['vaddr'], 16); int(p['want'], 16); int(p['new'], 16)

    os.makedirs(GEN, exist_ok=True)

    # ---- ul_config.h ----
    base = int(image_base, 16)
    lines = [
        '// 构建期生成 —— 请勿手改，勿提交',
        '#ifndef UL_CONFIG_H',
        '#define UL_CONFIG_H',
        '',
        'typedef struct { unsigned long long vaddr; unsigned int expect; unsigned int patch; } ul_patch_t;',
        '',
        '#define UL_BIN_NAME   "%s"' % bin_name,
        '#define UL_IMAGE_BASE 0x%xULL' % base,
        '#define UL_TEXT_SPAN  0x%s' % text_span,
        '#define UL_LOG_PATH   "%s"' % log_path,
        '#define UL_PATCH_COUNT %d' % len(patches),
        '',
        'static const ul_patch_t UL_PATCHES[] = {',
    ]
    for p in patches:
        lines.append('    { 0x%sULL, 0x%su, 0x%su },   /* %s */'
                     % (p['vaddr'], p['want'], p['new'], p.get('desc', '')))
    lines += ['};', '', '#endif', '']
    hdr = '\n'.join(lines)
    open(os.path.join(GEN, 'ul_config.h'), 'w').write(hdr)

    # 源码里的引导类名占位
    src = open(os.path.join(ROOT, 'tweak', 'tweak.m')).read()
    src = src.replace('UL_BOOTSTRAP_CLASS', boot_cls)
    open(os.path.join(GEN, 'tweak.m'), 'w').write(src)

    # ---- filter plist ----
    # 顶层必须直接就是 Filter。Bundles 按 bundle id 匹配；
    # Executables 按主二进制名匹配——两条独立路径，任一命中即注入。
    flt = {'Bundles': [bundle_id]}
    if bin_name and bin_name != bundle_id:
        flt['Executables'] = [bin_name]
    with open(os.path.join(GEN, 'ULPlugin.plist'), 'wb') as f:
        plistlib.dump({'Filter': flt}, f)
    print('   过滤规则: Bundles=%s Executables=%s'
          % (flt['Bundles'], flt.get('Executables', '-')))

    # ---- spec.json（供维护脚本与 DEB 构建使用）----
    json.dump({'version': version, 'app_name': app_name, 'bin_name': bin_name,
               'image_base': image_base, 'patches': patches},
              open(os.path.join(ROOT, 'spec.json'), 'w'), indent=1)

    print('generated:')
    for f in ('ul_config.h', 'tweak.m', 'ULPlugin.plist'):
        p = os.path.join(GEN, f)
        print('  %-20s %6d B  sha256=%s' % (f, os.path.getsize(p),
              hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]))
    print('  %-20s created' % 'spec.json')
    print('补丁数: %d   镜像基址: 0x%x' % (len(patches), base))


if __name__ == '__main__':
    main()
