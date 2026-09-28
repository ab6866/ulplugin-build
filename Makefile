# ============================================================================
#  Theos 工程（按 roothide 官方构建规范）
#
#  ★ ARCHS 必须同时包含 arm64 与 arm64e：
#    RootHide 环境按 arm64e 匹配，只出 arm64 会被静默跳过、插件毫无反应。
#    只有 macOS runner + Apple 官方 iOS SDK 才能产出真 arm64e
#    （Linux clang 编 arm64e 与 arm64 结果完全相同，ptrauth 指令 0 条）。
#
#  ★ <TWEAK_NAME>_LDFLAGS = -Wl,-no_fixup_chains 必须保留：
#    新版 ld 默认产出 chained fixups，构造器会被放进 __TEXT,__init_offsets
#    （4 字节/条、存 32 位相对偏移）。老式 Substrate/ElleKit 的初始化扫描器
#    只认 __DATA_CONST,__mod_init_func（存指针）→ 构造函数根本不执行，
#    现象就是「dylib 装上了但一行代码都没跑」。
#    ⚠ 该变量名带 TWEAK_NAME 前缀，改动 TWEAK_NAME 时这条会静默失配。
# ============================================================================

export ARCHS = arm64 arm64e
export TARGET = iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME ?= roothide

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = @@TWEAK_NAME@@
@@TWEAK_NAME@@_FILES = Tweak.x
@@TWEAK_NAME@@_CFLAGS = -O2 -fno-builtin -Wno-everything
@@TWEAK_NAME@@_FRAMEWORKS = Foundation CoreFoundation
@@TWEAK_NAME@@_LDFLAGS = -Wl,-no_fixup_chains

include $(THEOS_MAKE_PATH)/tweak.mk

# 仅 `make install` 使用；`make package` 不会据此生成 postinst。
# 安装/卸载时关闭目标进程靠 layout/DEBIAN 下的维护脚本。
INSTALL_TARGET_PROCESSES = seeulater
