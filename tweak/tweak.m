// ============================================================================
//  运行时改写插件（dylib）
//
//  原理：目标 App 用一个 @Observable 存储类持有权益状态属性。
//    getter : access(\.prop) 后 ldrb w0,[self+OFF]      -> 读状态
//    setter : 属性 setter 内 and wN,wM,#1               -> 写状态
//    赋值源 : prop = !verify(transaction)               -> 若干处 eor wN,wM,#1
//  把这些指令钉死为真值即解锁全部权益门控，不需要碰 StoreKit。
//
//  运行期改码：逐地址先校验原始机器码，不匹配就跳过，绝不在未知版本上盲写。
//
//  ★ 本文件不含任何目标特征：二进制名与补丁地址表都在构建期由 Secrets
//    生成到 ul_config.h。仓库内零真实标识。
//
//  ★ 链接必须用 -no_fixup_chains -adhoc_codesign（见 ci/build.sh 注释）。
// ============================================================================

#include <stdint.h>
#include <stddef.h>
#include "ul_config.h"

// 不依赖 libc 头（交叉编译时 glibc 头不可用）；原型手工声明，运行期由 libSystem 解析
extern void *memcpy(void *dst, const void *src, size_t n);
extern int   strcmp(const char *a, const char *b);
extern int   open(const char *, int, ...);
extern long  write(int, const void *, unsigned long);
extern int   close(int);
extern int   snprintf(char *, unsigned long, const char *, ...);

typedef unsigned long ul_size_t;

// ---------------------------------------------------------------- 常量
typedef unsigned int   kern_return_t;
typedef unsigned int   vm_prot_t;
typedef unsigned long  vm_size_t;
typedef unsigned long  vm_address_t;
typedef int            mach_port_t;

extern mach_port_t mach_task_self_(void);
#define UL_TASK_SELF mach_task_self_()

#define KERN_SUCCESS        0
#define VM_PROT_READ        0x1
#define VM_PROT_WRITE       0x2
#define VM_PROT_EXECUTE     0x4
#define VM_PROT_COPY        0x10

extern kern_return_t vm_protect(mach_port_t, vm_address_t, vm_size_t,
                                int, vm_prot_t);
extern const char *_dyld_get_image_name(uint32_t index);
extern const void   *_dyld_get_image_header(uint32_t index);

#define UL_O_WRONLY 0x0001
#define UL_O_CREAT  0x0200
#define UL_O_APPEND 0x0008

struct ul_mach_header_64 {
    uint32_t magic; int32_t cputype; int32_t cpusubtype; uint32_t filetype;
    uint32_t ncmds; uint32_t sizeofcmds; uint32_t flags; uint32_t reserved;
};
#define UL_MH_MAGIC_64    0xfeedfacfu
#define UL_CPU_TYPE_ARM64 0x0100000cu

#define UL_PAGE_MASK (~(uintptr_t)0x3FFF)
#define UL_PAGE_SIZE 0x4000

// ---------------------------------------------------------------- 日志
static int g_logfd = -1;

static int ul_open_log(void) {
    // 路径优先级参考真机验证可用的插件：先写 /var/mobile/Library/Logs/，再退到 /tmp/
    static const char *cands[] = {
        "/var/mobile/Library/Logs/ulmod.log",
        "/tmp/ulmod.log",
        "/var/tmp/ulmod.log",
    };
    for (unsigned i = 0; i < sizeof(cands) / sizeof(cands[0]); i++) {
        int fd = open(cands[i], UL_O_WRONLY | UL_O_CREAT | UL_O_APPEND, 0644);
        if (fd >= 0) return fd;
    }
    return -1;
}

static void ul_puts(const char *s) {
    if (g_logfd < 0 || !s) return;
    ul_size_t n = 0;
    while (s[n]) n++;
    write(g_logfd, s, n);
}

static void ul_log_hex(const char *tag, unsigned long v) {
    if (g_logfd < 0) return;
    char buf[160];
    int n = snprintf(buf, sizeof(buf), "%s%lx\n", tag, v);
    if (n > 0) {
        if ((ul_size_t)n > sizeof(buf) - 1) n = (int)sizeof(buf) - 1;
        write(g_logfd, buf, (ul_size_t)n);
    }
}

// ---------------------------------------------------------------- 目标校验
// 即使 filter 失效被注入到别的进程，也绝不误伤。
static int ul_is_target(void) {
    const char *exe = _dyld_get_image_name(0);
    if (!exe) return 0;
    const char *base = exe;
    for (const char *p = exe; *p; p++) if (*p == '/') base = p + 1;
    if (strcmp(base, UL_BIN_NAME) != 0) return 0;
    for (const char *p = exe; *p; p++)
        if (p[0] == '.' && p[1] == 'a' && p[2] == 'p' && p[3] == 'p' && p[4] == '/')
            return 1;
    return 0;
}

// ---------------------------------------------------------------- 打补丁
static int ul_apply(void) {
    const struct ul_mach_header_64 *hdr =
        (const struct ul_mach_header_64 *)_dyld_get_image_header(0);
    if (!hdr || hdr->magic != UL_MH_MAGIC_64) {
        ul_puts("[UL] !! 主镜像不是 64 位 Mach-O\n");
        return 0;
    }
    if (hdr->cputype != UL_CPU_TYPE_ARM64) {
        ul_puts("[UL] !! 主镜像非 arm64\n");
        return 0;
    }

    uint64_t slide = (uint64_t)(uintptr_t)hdr - (uint64_t)UL_IMAGE_BASE;
    ul_log_hex("[UL] slide = 0x", (unsigned long)slide);

    int applied = 0;
    for (unsigned i = 0; i < UL_PATCH_COUNT; i++) {
        const ul_patch_t *p = &UL_PATCHES[i];
        uint32_t *slot = (uint32_t *)(uintptr_t)(p->vaddr + slide);

        uint32_t got = 0;
        memcpy(&got, slot, sizeof(got));
        if (got != p->expect) {
            ul_log_hex("[UL] [SKIP] idx ", (unsigned long)i);
            ul_log_hex("[UL]        got  = 0x", (unsigned long)got);
            ul_log_hex("[UL]        want = 0x", (unsigned long)p->expect);
            continue;
        }

        uintptr_t page = (uintptr_t)slot & UL_PAGE_MASK;
        kern_return_t kr = vm_protect(UL_TASK_SELF, (vm_address_t)page,
                                      UL_PAGE_SIZE, 0,
                                      VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
        if (kr != KERN_SUCCESS) {
            ul_log_hex("[UL] [FAIL] vm_protect kr=", (unsigned long)kr);
            continue;
        }

        uint32_t nv = p->patch;
        memcpy(slot, &nv, sizeof(nv));

        uint32_t back = 0;
        memcpy(&back, slot, sizeof(back));
        vm_protect(UL_TASK_SELF, (vm_address_t)page, UL_PAGE_SIZE,
                   0, VM_PROT_READ | VM_PROT_EXECUTE);

        if (back == p->patch) {
            applied++;
            ul_log_hex("[UL] [OK]   idx ", (unsigned long)i);
        } else {
            ul_log_hex("[UL] [BAD]  回读不一致 idx ", (unsigned long)i);
            ul_log_hex("[UL]        back = 0x", (unsigned long)back);
        }
    }

    // AArch64: I-cache 与 D-cache 不同步，必须清理
    __builtin___clear_cache((char *)(uintptr_t)UL_IMAGE_BASE,
                            (char *)(uintptr_t)((uint64_t)UL_IMAGE_BASE + UL_TEXT_SPAN));
    ul_log_hex("[UL] === applied = ", (unsigned long)applied);
    ul_log_hex("[UL] === should  = ", (unsigned long)UL_PATCH_COUNT);
    return applied;
}

// ---------------------------------------------------------------- 主流程
static int g_done = 0;

static void ul_entry(void) {
    if (g_done) return;
    g_done = 1;

    if (g_logfd < 0) g_logfd = ul_open_log();
    ul_puts("[UL] ---- 插件已加载 ----\n");

    if (g_logfd < 0) return;   // 连日志都开不了，不冒险改码

    {
        const char *exe = _dyld_get_image_name(0);
        ul_puts("[UL] 主镜像: ");
        ul_puts(exe ? exe : "(null)");
        ul_puts("\n");
    }

    if (!ul_is_target()) {
        ul_puts("[UL] 非目标 app，跳过\n");
        close(g_logfd);
        g_logfd = -1;
        return;
    }

    int n = ul_apply();
    if (n == (int)UL_PATCH_COUNT)
        ul_puts("[UL] ★ 全部命中，改写已生效\n");
    else
        ul_puts("[UL] !! 未全部命中，见上方 SKIP/FAIL\n");

    close(g_logfd);
    g_logfd = -1;
}

// ---------------------------------------------------------------- 入口
// 用编译器**原生** constructor 机制：lld 会据此把函数指针写进
// __DATA_CONST,__mod_init_func 并生成对应重定位条目。
// （手工用 __attribute__((section)) 写死该段时 lld 不为它生成 rebase，
//   加载器不会修正该指针 —— 入口形同虚设。）
__attribute__((constructor)) static void ul_start(void) { ul_entry(); }
