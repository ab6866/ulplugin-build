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
# ---------------------------------------------------------------- 主流程
kill_app

APP_DIR=$(find_app)
if [ -z "$APP_DIR" ]; then
    echo "[ulmod] 未找到 $APP_NAME.app（dylib 层仍会生效）"
    exit 0
fi
BIN="$APP_DIR/$BIN_NAME"
BACKUP="$BIN.ulorig"
mkdir -p "$STATE_DIR" 2>/dev/null
ENT="$STATE_DIR/$BIN_NAME.entitlements"
echo "[ulmod] App: $APP_DIR"

if [ ! -f "$BACKUP" ]; then
    cp -p "$BIN" "$BACKUP" || { echo "[ulmod] 备份失败，放弃改写"; exit 0; }
    echo "[ulmod] 已备份原二进制"
fi

LDID=$(find_ldid) || { echo "[ulmod] !! 未找到 ldid，跳过静态改写（改签会闪退）"; exit 0; }

# 从**原始备份**现取 entitlements（备份始终是未改动的原始件）
if [ ! -s "$ENT" ]; then
    "$LDID" -e "$BACKUP" > "$ENT" 2>/dev/null
fi
if [ ! -s "$ENT" ]; then
    echo "[ulmod] !! 无法导出 entitlements，跳过静态改写"
    exit 0
fi
echo "[ulmod] 已获取原 entitlements"

# ---------------------------------------------------------------- 单点改写
# $1=十进制文件偏移 $2=原字节hex $3=新字节hex $4=新字节八进制转义
patch_one() {
    off="$1"; want="$2"; new="$3"; esc="$4"
    cur=$(od -An -tx1 -j "$off" -N 4 "$BIN" 2>/dev/null | tr -d ' \n')
    [ -n "$cur" ] || { echo "    [ERR ] $off 读取失败"; return 1; }
    if [ "$cur" = "$new" ]; then echo "    [OK  ] $off 已是目标字节"; return 0; fi
    if [ "$cur" != "$want" ]; then
        echo "    [SKIP] $off 原字节 $cur != $want（版本不符，跳过）"; return 1
    fi
    printf '%b' "$esc" | dd of="$BIN" bs=1 seek="$off" conv=notrunc >/dev/null 2>&1
    back=$(od -An -tx1 -j "$off" -N 4 "$BIN" 2>/dev/null | tr -d ' \n')
    if [ "$back" = "$new" ]; then echo "    [OK  ] $off $want -> $new"; return 0; fi
    echo "    [ERR ] $off 回读 $back != $new"; return 1
}

echo "[ulmod] 开始静态改写"
ok=0
{{PATCH_CALLS}}
echo "[ulmod] applied = $ok / {{NPATCH}}"

if [ "$ok" -gt "0" ]; then
    if "$LDID" -S"$ENT" "$BIN" 2>/dev/null; then
        if [ "$("$LDID" -e "$BIN" 2>/dev/null | wc -c)" -gt "10" ]; then
            echo "[ulmod] ★ 已用原 entitlements 重签并回读确认"
        else
            echo "[ulmod] !! 重签后 entitlements 为空，回滚"
            cp -p "$BACKUP" "$BIN"
        fi
    else
        echo "[ulmod] !! 重签失败，回滚"
        cp -p "$BACKUP" "$BIN"
    fi
fi

kill_app
echo "[ulmod] 完成，下次冷启动生效"
exit 0
'''

POSTRM_BODY = r'''
# entitlements 从**备份文件**现取 —— 备份本身就是未改动的原始件。
# 不依赖 /tmp：重启会清空 /tmp，若安装后重启过再卸载，
# 找不到 entitlements 就会「还原了但没重签」→ App 启动闪退。
kill_app

LDID=$(find_ldid)
[ -n "$LDID" ] || echo "[ulmod] !! 未找到 ldid，还原后无法重签（App 可能启动失败）"

for base in /var/containers/Bundle/Application /private/var/containers/Bundle/Application; do
    [ -d "$base" ] || continue
    for d in "$base"/*/"$APP_NAME.app"; do
        [ -d "$d" ] || continue
        BIN="$d/$BIN_NAME"
        BACKUP="$BIN.ulorig"
        [ -f "$BACKUP" ] || continue

        TMPENT="$BACKUP.ent"
        [ -n "$LDID" ] && "$LDID" -e "$BACKUP" > "$TMPENT" 2>/dev/null

        cp -p "$BACKUP" "$BIN" && echo "[ulmod] 已从备份还原"

        if [ -n "$LDID" ] && [ -s "$TMPENT" ]; then
            "$LDID" -S"$TMPENT" "$BIN" 2>/dev/null && echo "[ulmod] 已按原 entitlements 重签"
        fi
        rm -f "$TMPENT" "$BACKUP"
    done
done

kill_app
echo "[ulmod] 卸载完成"
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
