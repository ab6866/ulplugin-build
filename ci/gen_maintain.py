#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
生成 DEB 的 postinst / postrm 维护脚本。

方案（与已实机验证有效的静态改写走同一条路）：
  postinst 直接改写**已安装 App 的主二进制** —— 不依赖注入链路是否可用。
  dylib 注入作为第二层保留。

spec（JSON，字段全部与真实特征无关）：
  { "scheme": "rootless"|"roothide", "app_name": "...", "bin_name": "...",
    "image_base": "100000000",
    "patches": [{"vaddr":"...","want":"...","new":"..."}, ...] }

关键点（全部来自实测踩坑）：
  1) entitlements 用 `ldid -e` 从**原始备份**现取再 `ldid -S` 重签。
     `ldid -S` 不带参数会抹掉 entitlements → 云端同步能力失效，必须显式传文件。
  2) 没有 ldid 就**完全跳过改写**（改签缺失 → 签名失效 → 启动闪退），由 dylib 层兜底。
  3) 逐点 `od` 校验原字节；不匹配跳过；已是目标字节算成功（幂等）。
  4) 写盘用 `printf '%b'` 的**固定 3 位八进制**转义（BusyBox printf 不支持 \xNN；
     写成 \0ddd 会让 0x00 变成 \0000，printf 只吃 3 位，多出的 '0' 会写进二进制）。
  5) 忙等替代 sleep；纯 POSIX；trap 兜底绝不阻断 dpkg。
"""
import json, os, sys


def octesc(hexstr):
    """'20008052' -> '\\040\\000\\200\\122'  固定 3 位八进制（BusyBox 安全）"""
    return ''.join('\\%03o' % int(hexstr[i:i + 2], 16)
                   for i in range(0, len(hexstr), 2))


HDR = r'''#!/bin/sh
# ============================================================================
#  {{TITLE}}（{{SCHEME}}）
#  纯 POSIX，不依赖 sleep / command -v；任何情况都不阻断 dpkg
# ============================================================================
trap 'exit 0' EXIT

APP_NAME="{{APP_NAME}}"
BIN_NAME="{{BIN_NAME}}"
STATE_DIR="/tmp/.ulmod"

kill_app() {
    f=0
    for c in /var/jb/bin/launchctl /var/jb/usr/bin/launchctl /bin/launchctl /usr/bin/launchctl; do
        if [ -x "$c" ]; then "$c" killall "$BIN_NAME" >/dev/null 2>&1 && f=1; break; fi
    done
    if [ "$f" = "0" ]; then
        for k in /var/jb/usr/bin/killall /var/jb/bin/killall /usr/bin/killall /bin/killall; do
            if [ -x "$k" ]; then "$k" -9 "$BIN_NAME" >/dev/null 2>&1 && f=1; break; fi
        done
    fi
    if [ "$f" = "0" ]; then
        for p in /proc/[0-9]*; do
            [ -r "$p/comm" ] || continue
            [ "$(cat "$p/comm" 2>/dev/null)" = "$BIN_NAME" ] || continue
            kill -9 "${p#/proc/}" >/dev/null 2>&1
            f=1
        done
    fi
    i=0
    while [ "$i" -lt 300 ]; do
        a=0
        for p in /proc/[0-9]*; do
            [ -r "$p/comm" ] || continue
            [ "$(cat "$p/comm" 2>/dev/null)" = "$BIN_NAME" ] && a=1
        done
        [ "$a" = "0" ] && break
        i=$((i + 1))
    done
}

find_ldid() {
    for L in /var/jb/usr/bin/ldid /var/jb/bin/ldid /usr/bin/ldid /usr/local/bin/ldid; do
        [ -x "$L" ] && { printf '%s' "$L"; return 0; }
    done
    return 1
}

find_app() {
    for base in /var/containers/Bundle/Application /private/var/containers/Bundle/Application; do
        [ -d "$base" ] || continue
        for d in "$base"/*/"$APP_NAME.app"; do
            [ -d "$d" ] && [ -f "$d/$BIN_NAME" ] && { printf '%s' "$d"; return 0; }
        done
    done
    return 1
}
'''

POSTINST_BODY = r'''
# 纯 dylib hook 版：postinst 只负责结束目标进程，
# 让下次冷启动重新注入 dylib。不改动任何 App 文件，
# 因此不涉及签名、entitlements、备份与还原。
kill_app

if [ -d /var/containers/Bundle/Application ] || [ -d /private/var/containers/Bundle/Application ]; then
    echo "[ulmod] 已结束目标进程；dylib 将在下次冷启动注入并 hook"
else
    echo "[ulmod] 未检测到 App 容器（dylib 仍会按 filter 注入）"
fi
echo "[ulmod] 日志：/tmp/ulmod.log"
exit 0
'''


POSTRM_BODY = r'''
# 纯 dylib hook 版：卸载无需还原任何文件，只需结束进程让 dylib 不再注入。
kill_app
echo "[ulmod] 卸载完成；目标 App 已结束，重新打开即为原样"
exit 0
'''




def build(spec, outdir):
    os.makedirs(outdir, exist_ok=True)
    base = int(spec['image_base'], 16)

    calls = []
    for p in spec['patches']:
        off = int(p['vaddr'], 16) - base          # 文件偏移 = vaddr - __TEXT vmaddr
        calls.append('patch_one %d %s %s "%s" && ok=$((ok+1))'
                     % (off, p['want'], p['new'], octesc(p['new'])))

    common = {
        '{{SCHEME}}': spec['scheme'],
        '{{APP_NAME}}': spec['app_name'],
        '{{BIN_NAME}}': spec['bin_name'],
        '{{NPATCH}}': str(len(spec['patches'])),
        '{{PATCH_CALLS}}': '\n'.join(calls),
    }

    for name, title, tail in (
            ('postinst', '运行时改写：结束进程 → 静态改写 → 按原 entitlements 重签', POSTINST_BODY),
            ('postrm', '卸载：从备份还原 → 重签 → 结束进程', POSTRM_BODY)):
        text = HDR.replace('{{TITLE}}', title) + tail
        for k, v in common.items():
            text = text.replace(k, v)
        path = os.path.join(outdir, '%s.%s' % (name, spec['scheme']))
        with open(path, 'w') as f:
            f.write(text)
        os.chmod(path, 0o755)
        print('generated %s (%d B)' % (os.path.basename(path), os.path.getsize(path)))


if __name__ == '__main__':
    build(json.load(open(sys.argv[1])),
          sys.argv[2] if len(sys.argv) > 2 else 'dsc')
