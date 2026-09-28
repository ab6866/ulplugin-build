# ============================================================================
#  Theos 工程（照抄真机验证成功项目的构建方式）
#
#  ★ 为什么必须用 Theos + macOS runner：
#    Linux/iSH 的 clang **无法生成 arm64e 代码**（arm64/arm64e 编译结果完全
#    相同，ptrauth 指令均为 0 条）。而 RootHide 环境需要 arm64e 切片。
#    只有 macOS runner + Apple 官方 iOS SDK 才能产出真 arm64e。
#
#  ★ 为什么必须 -no_fixup_chains：
#    新版 ld 默认产出 chained fixups，%ctor 会被放进 __TEXT,__init_offsets
#    （4 字节/条、存 32 位相对偏移）。老式 Substrate/ElleKit 的初始化扫描器
#    只认 __DATA_CONST,__mod_init_func（存指针）→ 构造函数根本不执行，
#    表现为「dylib 装上了但一行代码都没跑」。
# ============================================================================

export ARCHS = arm64 arm64e
export TARGET = iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME ?= roothide

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = Tweak
Tweak_FILES = Tweak.x
Tweak_CFLAGS = -O2 -fno-builtin -Wno-everything
Tweak_FRAMEWORKS = Foundation CoreFoundation
Tweak_LDFLAGS = -Wl,-no_fixup_chains

include $(THEOS_MAKE_PATH)/tweak.mk

INSTALL_TARGET_PROCESSES = @@TARGET_PROCS@@
