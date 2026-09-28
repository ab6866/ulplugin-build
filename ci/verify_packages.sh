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
        RB=$(llvm-otool -l "/tmp/vfy_$arch.dylib" | grep -c LC_DYLD_CHAINED_FIXUPS || true)
        echo "    [$arch] cpusubtype=$SUB ptrauth=$PTR __init_offsets=$IO chained=$RB 签名=$SG"
        # 成功产物形态：__init_offsets>=1 且 LC_DYLD_CHAINED_FIXUPS>=1（现代 Theos 默认）
        [ "${IO:-0}" -ge 1 ] || [ "${MI:-0}" -ge 1 ] || { echo "      !! 既无 __init_offsets 也无 __mod_init_func（入口段缺失）"; fails=$((fails+1)); }
        [ "${SG:-0}" -ge 1 ] || { echo "      !! 缺代码签名（amfid 会静默拒载）"; fails=$((fails+1)); }
    done

    # ★ 最关键的一条：arm64e 切片必须含真 ptrauth 指令。
    #   若为 0，说明是用 Linux clang 编的"伪 arm64e"（手工改 cpusubtype），
    #   dyld 会按 arm64e 规则认证从未签名的指针 → 构造器不执行 → 装了没反应。
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
