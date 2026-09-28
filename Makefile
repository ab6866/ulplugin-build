# ============================================================================
#  Theos 工程
#
#  ★ 必须 macOS runner：只有 Apple 官方 iOS SDK 能产出真 arm64e 切片
#    （实拍证据：macOS 上 `-arch arm64e` 的 pac/aut 指令数 = 1；
#     而 Linux clang 编 arm64e 与 arm64 完全相同、ptrauth = 0）。
#    参考项目的成功产物是 fat(arm64 + arm64e)，arm64e 切片含 23 条 ptrauth
#    指令；而它的中间产物是手工把 cpusubtype 改成 2 的**伪 arm64e**，
#    那才是"装了没反应"的版本。
#
#  ★ 不要加 -no_fixup_chains：
#    Apple ld 在 arm64e 上会直接警告并忽略它
#      "bind opcodes are no longer supported with arm64e, switching to chained fixups"
#    实测成功产物的形态就是 __init_offsets + LC_DYLD_CHAINED_FIXUPS。
# ============================================================================

export ARCHS = arm64 arm64e
export TARGET = iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME ?= roothide

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = @@TWEAK_NAME@@
@@TWEAK_NAME@@_FILES = Tweak.x
@@TWEAK_NAME@@_CFLAGS = -O2 -fno-builtin -Wno-everything
@@TWEAK_NAME@@_FRAMEWORKS = Foundation CoreFoundation

# ★★ 必须加 -no_fixup_chains：
#   新版 ld 默认产出 chained fixups，%ctor 会被放进 __TEXT,__init_offsets
#   （4 字节/条、存 32 位相对偏移）。而老式 Substrate/ElleKit 的初始化扫描器
#   只认 __DATA_CONST,__mod_init_func（存指针）→ 构造函数根本不执行，
#   表现为「dylib 装上了但一行代码都没跑」。
@@TWEAK_NAME@@_LDFLAGS = -Wl,-no_fixup_chains

include $(THEOS_MAKE_PATH)/tweak.mk

INSTALL_TARGET_PROCESSES = @@TARGET_PROCS@@
