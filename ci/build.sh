#!/bin/sh
# ============================================================================
#  一次产出双无根 DEB 与注入版 IPA
#  依赖环境变量（GitHub Secrets 注入）：见 README
#  本脚本内不含任何目标 App 特征。
# ============================================================================
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TW="$ROOT/tweak"
GEN="$TW/generated"
OUT="$ROOT/dist"
mkdir -p "$OUT"

echo "=============================================================="
echo " 1/5  生成目标特征（ul_config.h / filter plist / spec.json）"
echo "=============================================================="
python3 "$ROOT/ci/gen_src.py"

echo
echo "=============================================================="
echo " 2/5  编译插件 dylib（三入口：init_offsets / mod_init_func / +load）"
echo "=============================================================="
CC=${CC:-clang}
LD=${LD:-ld64.lld}

# -ffreestanding: 用 clang 自带的 freestanding 头，避免交叉编译时拉到宿主 glibc 头
CCFLAGS="-x objective-c -target arm64-apple-ios15.0 -O2 -fno-objc-arc -ffreestanding -I$GEN"
$CC -c $CCFLAGS -o "$GEN/tweak.o" "$GEN/tweak.m"

link_dylib() {
    # $1=输出 $2=install_name
    # ★★ 两个必须的链接参数（都来自实机踩坑，缺一即静默失效）：
    #   1) -no_fixup_chains  —— 产出 __DATA_CONST,__mod_init_func（老式扫描器只认这个）。
    #      用 -fixup_chains 会产出 __TEXT,__init_offsets，RootHide/ElleKit 无法处理；
    #      实测且真机验证过的参考包一律 __init_offsets=0 / LC_DYLD_INFO_ONLY。
    #   2) -adhoc_codesign   —— iOS 上 amfid 拒绝加载**无签名**的 dylib，且失败静默。
    $LD -dylib -arch arm64 -platform_version ios 15.0 17.0 \
        -install_name "$2" \
        -undefined dynamic_lookup -no_fixup_chains -adhoc_codesign \
        -o "$1" "$GEN/tweak.o"

    # 二次保险：若链接器未产出签名，用 ldid 补一个 ad-hoc 签名
    if ! llvm-otool -l "$1" 2>/dev/null | grep -q LC_CODE_SIGNATURE; then
        if command -v ldid >/dev/null 2>&1; then
            echo "  (链接器未签名，改用 ldid -S 补签)"
            ldid -S "$1"
        else
            echo "  !! 警告：$1 无代码签名，设备上可能静默加载失败"
        fi
    fi
}

link_dylib "$TW/ULPlugin_rootless.dylib" "@rpath/ULPlugin.dylib"
link_dylib "$TW/ULPlugin_roothide.dylib" \
    "@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries/ULPlugin.dylib"

echo "--- 入口段验收（纯 Python 解析 Mach-O，跨平台）---"
python3 "$ROOT/ci/check_dylib.py" "$TW/ULPlugin_rootless.dylib" "@rpath/ULPlugin.dylib"
python3 "$ROOT/ci/check_dylib.py" "$TW/ULPlugin_roothide.dylib" \
    "@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries/ULPlugin.dylib"

echo
echo "=============================================================="
echo " 3/5  组装 DEB（rootless / roothide）"
echo "=============================================================="
python3 "$ROOT/ci/build_deb.py"

echo
echo "=============================================================="
echo " 4/5  DEB 验收"
echo "=============================================================="
for d in "$OUT"/*.deb; do
    echo "--- $(basename "$d")"
    dpkg-deb -I "$d" | grep -E "Package:|Architecture:|Version:|Conflicts:|Replaces:"
    dpkg-deb -c "$d" | sed 's/^/    /'
    dpkg-deb --ctrl-tarfile "$d" | tar -tf - 2>/dev/null | sed 's/^/    ctl: /' || true
done

echo
echo "=============================================================="
echo " 5/5  注入版 IPA（可选，需 UL_IPA 指向原始 IPA）"
echo "=============================================================="
if [ -n "$UL_IPA" ] && [ -f "$UL_IPA" ]; then
    python3 "$ROOT/ci/patch_ipa.py" "$UL_IPA" "$OUT/ULPlugin.ipa"
else
    echo "  跳过（未提供 UL_IPA）"
fi

echo
echo "全部产物："
ls -la "$OUT"
