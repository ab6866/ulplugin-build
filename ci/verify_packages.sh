#!/bin/sh
# ============================================================================
#  产物验收：把 Theos 产出的包整理到 dist/，并做架构与入口硬校验
# ============================================================================
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$ROOT/dist"
cd "$ROOT"

# 找出 Theos 产出的包，重命名为中性名
i=0
for p in packages/*.deb; do
    [ -f "$p" ] || continue
    i=$((i + 1))
    if grep -q "roothide" "$p" 2>/dev/null || dpkg-deb -f "$p" Package 2>/dev/null | grep -q roothide; then
        cp "$p" "$ROOT/dist/tweak-roothide.deb"
    else
        cp "$p" "$ROOT/dist/tweak-rootless.deb"
    fi
done

echo "=== dist 产物 ==="
ls -la "$ROOT/dist/"

fails=0
for d in "$ROOT/dist"/*.deb; do
    [ -f "$d" ] || { echo "!! 无产物"; exit 1; }
    echo
    echo "=========== $(basename "$d") ==========="
    dpkg-deb -f "$d" Package Version Architecture 2>/dev/null | sed 's/^/  /'
    dpkg-deb -c "$d" | grep -v "^tar:" | sed 's/^/  /'

    rm -rf /tmp/vfy && mkdir -p /tmp/vfy
    dpkg-deb -x "$d" /tmp/vfy 2>/dev/null
    DY=$(find /tmp/vfy -name '*.dylib' | head -1)
    [ -n "$DY" ] || { echo "  !! 包内无 dylib"; fails=$((fails+1)); continue; }

    echo "  ---- dylib 架构 ----"
    llvm-lipo -info "$DY" | sed 's/^/    /'

    for arch in arm64 arm64e; do
        llvm-lipo -thin "$arch" "$DY" -output "/tmp/vfy_$arch.dylib" 2>/dev/null || {
            echo "    !! 缺 $arch 切片"
            fails=$((fails+1))
            continue
        }
        SUB=$(python3 -c "
import struct,sys
d=open(sys.argv[1],'rb').read()
print(struct.unpack_from('<i',d,8)[0])" "/tmp/vfy_$arch.dylib")
        PTR=$(llvm-objdump -d "/tmp/vfy_$arch.dylib" 2>/dev/null | grep -cE 'paciasp|autiasp|pacibsp|autibsp' || true)
        IO=$(llvm-otool -l "/tmp/vfy_$arch.dylib" | grep -c __init_offsets || true)
        MI=$(llvm-objdump -h "/tmp/vfy_$arch.dylib" 2>/dev/null | grep -c mod_init_func || true)
        SG=$(llvm-otool -l "/tmp/vfy_$arch.dylib" | grep -c LC_CODE_SIGNATURE || true)
        RB=$(llvm-objdump --macho --rebase "/tmp/vfy_$arch.dylib" 2>/dev/null | grep -c mod_init || true)
        echo "    [$arch] cpusubtype=$SUB ptrauth=$PTR __init_offsets=$IO mod_init=$MI 签名=$SG 重定位=$RB"
        [ "${IO:-0}" = "0" ] || { echo "      !! __init_offsets 必须为 0"; fails=$((fails+1)); }
        [ "${MI:-0}" -ge 1 ] || { echo "      !! 缺 __mod_init_func"; fails=$((fails+1)); }
        [ "${SG:-0}" -ge 1 ] || { echo "      !! 缺代码签名"; fails=$((fails+1)); }
        [ "${RB:-0}" -ge 1 ] || { echo "      !! __mod_init_func 无重定位（入口不会被调用）"; fails=$((fails+1)); }
    done

    # arm64e 切片必须真有 ptrauth 指令
    if [ -f /tmp/vfy_arm64e.dylib ]; then
        E=$(llvm-objdump -d /tmp/vfy_arm64e.dylib 2>/dev/null | grep -cE 'paciasp|autiasp|pacibsp|autibsp' || true)
        if [ "${E:-0}" -gt 0 ]; then
            echo "    ★ arm64e 切片含 $E 条 ptrauth 指令（真 arm64e，非伪装）"
        else
            echo "    !! arm64e 切片无 ptrauth 指令 —— 可能是伪造的 cpusubtype"
            fails=$((fails+1))
        fi
    fi

    # filter
    PL=$(find /tmp/vfy -name '*.plist' | head -1)
    if [ -n "$PL" ]; then
        echo "  ---- 注入过滤 ----"
        python3 -c "
import plistlib,sys
d=plistlib.load(open(sys.argv[1],'rb'))
print('    顶层:', list(d.keys()))
print('    Bundles:', d.get('Filter',{}).get('Bundles'))
" "$PL" | sed 's/^/  /'
    fi
done

echo
if [ "$fails" != "0" ]; then
    echo "!!!!! 验收发现 $fails 个问题"
    exit 1
fi
echo "★★★★★ 全部验收通过"
