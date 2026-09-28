#!/bin/sh
# ============================================================================
#  产物验收：把 Theos 产出的包整理到 dist/，并做架构与入口硬校验
# ============================================================================
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)

# macOS runner 用 Apple 自带工具；Linux 用 llvm-* 前缀
if command -v lipo >/dev/null 2>&1; then
    LIPO=lipo; OTOOL=otool; OBJDUMP=objdump
else
    LIPO=llvm-lipo; OTOOL=llvm-otool; OBJDUMP=llvm-objdump
fi
echo "工具: $LIPO / $OTOOL / $OBJDUMP"
mkdir -p "$ROOT/dist"
cd "$ROOT"

# 找出 Theos 产出的包，重命名为中性名
i=0
for p in packages/*.deb; do
    [ -f "$p" ] || continue
    i=$((i + 1))
    ARCH=$(dpkg-deb -f "$p" Architecture 2>/dev/null || echo "")
    case "$ARCH" in
        *arm64e*) cp "$p" "$ROOT/dist/tweak-roothide.deb" ;;
        *)        cp "$p" "$ROOT/dist/tweak-rootless.deb" ;;
    esac
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
    LIPO -info "$DY" | sed 's/^/    /'

    for arch in arm64 arm64e; do
        LIPO -thin "$arch" "$DY" -output "/tmp/vfy_$arch.dylib" 2>/dev/null || {
            echo "    !! 缺 $arch 切片"
            fails=$((fails+1))
            continue
        }
        SUB=$(python3 -c "
import struct,sys
d=open(sys.argv[1],'rb').read()
print(struct.unpack_from('<i',d,8)[0])" "/tmp/vfy_$arch.dylib")
        PTR=$(OBJDUMP -d "/tmp/vfy_$arch.dylib" 2>/dev/null | grep -cE 'paciasp|autiasp|pacibsp|autibsp' || true)
        IO=$(OTOOL -l "/tmp/vfy_$arch.dylib" | grep -c __init_offsets || true)
        MI=$(OBJDUMP -h "/tmp/vfy_$arch.dylib" 2>/dev/null | grep -c mod_init_func || true)
        SG=$(OTOOL -l "/tmp/vfy_$arch.dylib" | grep -c LC_CODE_SIGNATURE || true)
        RB=$(OTOOL -l "/tmp/vfy_$arch.dylib" | grep -c LC_DYLD_CHAINED_FIXUPS || true)
        echo "    [$arch] cpusubtype=$SUB ptrauth=$PTR __init_offsets=$IO chained=$RB 签名=$SG"
        # 成功产物形态：__init_offsets>=1 且 LC_DYLD_CHAINED_FIXUPS>=1（现代 Theos 默认）
        [ "${IO:-0}" -ge 1 ] || [ "${MI:-0}" -ge 1 ] || { echo "      !! 既无 __init_offsets 也无 __mod_init_func（入口段缺失）"; fails=$((fails+1)); }
        [ "${SG:-0}" -ge 1 ] || { echo "      !! 缺代码签名（amfid 会静默拒载）"; fails=$((fails+1)); }
    done

    # ★ 最关键的一条：arm64e 切片必须含真 ptrauth 指令。
    #   若为 0，说明是用 Linux clang 编的"伪 arm64e"（手工改 cpusubtype），
    #   dyld 会按 arm64e 规则认证从未签名的指针 → 构造器不执行 → 装了没反应。
    if [ -f /tmp/vfy_arm64e.dylib ]; then
        E=$(OBJDUMP -d /tmp/vfy_arm64e.dylib 2>/dev/null | grep -cE 'paciasp|autiasp|pacibsp|autibsp' || true)
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
