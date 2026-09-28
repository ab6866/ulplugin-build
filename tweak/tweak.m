// ============================================================================
//  运行时改写插件（运行期改码）
//
//  原理：目标 App 用一个 @Observable 存储类持有唯一的状态字段属性。
//    读取器 : access(\.xxx) 后 ldrb w0,[self+OFF]
//    写入器 : 在该属性 setter 里做 and 掩码
//    赋值源 : has= !verify(transaction)  —— 若干处 eor wN,wM,#1
//  把这几处钉死为 true，即运行时改写全部状态，不需要碰 StoreKit。
//
//  本插件在 dyld 加载时就地改写主程序这几条指令。逐地址先校验原始机器码，
//  不匹配就跳过，绝不在未知版本上盲写。
//
//  ★ 本文件不含任何目标 App 特征：二进制名与补丁地址表均在构建期由 Secrets
//    生成到 ul_config.h。仓库内零真实标识。
// ============================================================================

#include <stdint.h>
#include <stddef.h>
#include "ul_config.h"

// 不依赖 libc 头（交叉编译时 glibc 头不可用）；原型手工声明，运行期由 libSystem 解析
extern void *memcpy(void *dst, const void *src, size_t n);
extern int   strcmp(const char *a, const char *b);
extern void  __builtin___clear_cache(char *, char *);

// ---------------------------------------------------------------- 自带声明
// 不依赖 iOS SDK 头，全部手工声明（ABI 稳定，与系统实现一致）
typedef unsigned long   ul_size_t;
typedef long            ul_ssize_t;
typedef unsigned int    kern_return_t;
typedef unsigned int    vm_prot_t;
typedef unsigned long   vm_size_t;
typedef unsigned long   vm_address_t;
typedef int             mach_port_t;

extern mach_port_t mach_task_self_(void);
#define mach_task_self() mach_task_self_()

#define KERN_SUCCESS        0
#define VM_PROT_READ        0x1
#define VM_PROT_WRITE       0x2
#define VM_PROT_EXECUTE     0x4
#define VM_PROT_COPY        0x10

extern kern_return_t vm_protect(mach_port_t, vm_address_t, vm_size_t,
                                int, vm_prot_t);
extern const char *_dyld_get_image_name(uint32_t index);
extern const void   *_dyld_get_image_header(uint32_t index);
extern int open(const char *, int, ...);
extern ul_ssize_t write(int, const void *, ul_size_t);
extern int close(int);
extern int snprintf(char *, ul_size_t, const char *, ...);

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
// 写多个候选路径，任一成功即用。
// 注意：App 进程里的 /tmp 是**沙盒内**的 tmp，与外部的 /var/tmp 不是一回事；
// 所以两条都试，并用 getenv("HOME") 拼一个沙盒内绝对路径做兜底。
static int g_logfd = -1;

extern char *getenv(const char *);

static int ul_open_log(void) {
    static const char *cands[] = {
        "/var/tmp/ulmod.log",           // 全局（越狱环境通常可写，最好找）
        "/tmp/ulmod.log",               // 本进程视角的 tmp
        NULL, NULL
    };
    char buf[256];
    // 沙盒内：<HOME>/Documents/ulmod.log（HOME 形如 .../Data/Application/<UUID>）
    char *home = getenv("HOME");
    if (home) {
        unsigned long n = 0;
        while (home[n] && n < sizeof(buf) - 32) { buf[n] = home[n]; n++; }
        const char *tail = "/Documents/ulmod.log";
        unsigned long k = 0;
        while (tail[k]) { buf[n + k] = tail[k]; k++; }
        buf[n + k] = 0;
        cands[2] = buf;
    }
    for (int i = 0; cands[i]; i++) {
        int fd = open(cands[i], UL_O_WRONLY | UL_O_CREAT | UL_O_APPEND, 0644);
        if (fd >= 0) return fd;
    }
    return -1;
}

static void ul_puts(const char *s) {
    if (g_logfd < 0 || !s) return;
    ul_size_t n = 0;
    while (s[n]) n++;
    (void)write(g_logfd, s, n);
}

static void ul_log_num(const char *head, unsigned long v1, const char *mid,
                       unsigned long v2, const char *tail) {
    if (g_logfd < 0) return;
    char buf[240];
    int n = snprintf(buf, sizeof(buf), "%s%lx%s%lx%s", head, v1, mid, v2, tail);
    if (n > 0) {
        if ((ul_size_t)n > sizeof(buf) - 1) n = (int)(sizeof(buf) - 1);
        (void)write(g_logfd, buf, (ul_size_t)n);
    }
}

// ---------------------------------------------------------------- 目标校验
// 即使 filter plist 失效被注入到别的进程，也绝不误伤。
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
        ul_puts("[UL] !! 主镜像不是 64 位 Mach-O，放弃\n");
        return 0;
    }
    if (hdr->cputype != UL_CPU_TYPE_ARM64) {
        ul_puts("[UL] !! 主镜像非 arm64，放弃\n");
        return 0;
    }

    uint64_t slide = (uint64_t)(uintptr_t)hdr - (uint64_t)UL_IMAGE_BASE;
    ul_log_num("[UL] slide = 0x", (unsigned long)slide, "\n", 0, "");

    int applied = 0;
    for (ul_size_t i = 0; i < UL_PATCH_COUNT; i++) {
        const ul_patch_t *p = &UL_PATCHES[i];
        uint32_t *slot = (uint32_t *)(uintptr_t)(p->vaddr + slide);

        uint32_t got = 0;
        memcpy(&got, slot, sizeof(got));
        if (got != p->expect) {
            ul_log_num("[UL] [SKIP] idx=", (unsigned long)i,
                       " got=", (unsigned long)got, "\n");
            continue;
        }

        uintptr_t page = (uintptr_t)slot & UL_PAGE_MASK;
        kern_return_t kr = vm_protect(mach_task_self(), (vm_address_t)page,
                                      UL_PAGE_SIZE, 0,
                                      VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
        if (kr != KERN_SUCCESS) {
            ul_log_num("[UL] [FAIL] vm_protect idx=", (unsigned long)i,
                       " kr=", (unsigned long)kr, "\n");
            continue;
        }

        uint32_t nv = p->patch;
        memcpy(slot, &nv, sizeof(nv));

        uint32_t back = 0;
        memcpy(&back, slot, sizeof(back));
        (void)vm_protect(mach_task_self(), (vm_address_t)page, UL_PAGE_SIZE,
                         0, VM_PROT_READ | VM_PROT_EXECUTE);

        if (back == p->patch) {
            applied++;
            ul_log_num("[UL] [OK]   idx=", (unsigned long)i, "\n", 0, "");
        } else {
            ul_log_num("[UL] [BAD]  回读不一致 idx=", (unsigned long)i,
                       " back=", (unsigned long)back, "\n");
        }
    }

    // AArch64: I-cache 与 D-cache 不同步，必须清理
    __builtin___clear_cache((char *)(uintptr_t)UL_IMAGE_BASE,
                            (char *)(uintptr_t)((uint64_t)UL_IMAGE_BASE + UL_TEXT_SPAN));
    ul_log_num("[UL] === applied = ", (unsigned long)applied, " / ",
               (unsigned long)UL_PATCH_COUNT, " ===\n");
    return applied;
}

// ---------------------------------------------------------------- 入口
static int g_done = 0;

static void ul_entry(void) {
    if (g_done) return;
    g_done = 1;

    if (g_logfd < 0) g_logfd = ul_open_log();
    ul_puts("[UL] ul_entry\n");

    if (g_logfd < 0) g_logfd = ul_open_log();
    ul_puts("[UL] === ul_entry 被调用 ===\n");

    if (!ul_is_target()) {
        ul_puts("[UL] 非目标 app，跳过\n");
        if (g_logfd >= 0) close(g_logfd);
        g_logfd = -1;
        return;
    }

    ul_puts("[UL] 改写插件启动\n");
    int n = ul_apply();
    if (n == (int)UL_PATCH_COUNT)
        ul_puts("[UL] ★ 改写已生效\n");
    else
        ul_puts("[UL] !! 未全部命中，见上方 SKIP/FAIL\n");

    if (g_logfd >= 0) close(g_logfd);
    g_logfd = -1;
}

// ---------------------------------------------------------------- 入口
// ★ 只保留一条入口：__DATA_CONST,__mod_init_func。
//   这是真机验证过的形态（参考包同为 __init_offsets=0 + __mod_init_func size=8）。
//   不要再加 ObjC +load 或 constructor —— 它们会往同一段追加条目，
//   使 size 变成 16，偏离已验证形态。
__attribute__((used, section("__DATA_CONST,__mod_init_func")))
static void (*ul_mod_init[1])(void) = { ul_entry };
