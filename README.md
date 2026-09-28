# Mod Plugin

iOS 越狱环境下的 App 状态改写插件构建管线。产出**双无根 DEB**
（双方案（标准 / 备用））与可选**注入版 IPA**。

仓库内**不含任何目标 App 特征** —— 二进制名、bundle id、补丁地址表全部在
构建期由 GitHub Actions Secrets 注入，生成物已 `.gitignore`。

---

## 工作原理

目标 App 用一个 `@Observable` 存储类持有唯一的状态字段属性，其结构为：

| 位置 | 形态 |
|---|---|
| 读取器 | `Observation.access(\.prop)` 后 `ldrb w0, [self, #OFF]` |
| 写入器 | 属性 setter 内 `and wN, wM, #1` |
| 赋值源 | `prop = !verify(transaction)` —— 若干处 `eor wN, wM, #1` |

把这几条指令钉死为真值（`mov wN, #1`），即运行时改写全部状态门控，
**不需要触碰 StoreKit、票据或网络请求**。

插件在 dyld 加载镜像时就地改写主程序指令，逐地址先校验原始机器码，
不匹配即跳过，绝不在未知版本上盲写。

### 两套交付，同一个结果

| 交付物 | 机制 | 依赖 |
|---|---|---|
| **注入版 IPA** | 构建期静态改写机器码 | 无（改完即生效） |
| **DEB** | ① `postinst` 静态改写已安装的二进制（主）<br>② 注入 dylib 运行期改码（备） | ①需要 `ldid`<br>②需要注入链路可用 |

两条路改出来的二进制应当**逐字节等价**（可从 `sha256` 核对）。

---

## 目录结构

```
.github/workflows/build.yml   构建 + 验收 + Release
tweak/tweak.m                 插件源码（三入口，无 App 特征）
ci/gen_src.py                 Secrets → ul_config.h / filter plist / spec.json
ci/build.sh                   一键：生成 → 编译 → 组装 → 验收
ci/build_deb.py               组装双无根 DEB
ci/gen_maintain.py            生成 postinst / postrm
ci/patch_ipa.py               注入版 IPA + 重签
```

---

## 必需的 Secrets

| Secret | 说明 | 示例形态 |
|---|---|---|
| `UL_APP_NAME` | App 包名（不含 `.app`） | `<AppName>` |
| `UL_BIN_NAME` | 主可执行文件名 | `<binName>` |
| `UL_BUNDLE_ID` | bundle id，用于绑定注入 | `com.example.app` |
| `UL_IMAGE_BASE` | `__TEXT` 段 vmaddr（十六进制，无 `0x`） | `100000000` |
| `UL_TEXT_SPAN` | `__clear_cache` 覆盖范围（十六进制） | `600000` |
| `UL_PATCHES` | 补丁表 JSON 数组 | 见下 |
| `UL_LOG_PATH` | 运行期日志路径（可选） | `/tmp/ulmod.log` |
| `UL_BOOTSTRAP_CLASS` | ObjC 引导类名（可选，需唯一） | `ULBootstrap` |

### `UL_PATCHES` 格式

```json
[
  {"vaddr": "10037b398", "want": "60824039", "new": "20008052", "desc": "getter"},
  {"vaddr": "10037d964", "want": "15000012", "new": "35008052", "desc": "setter"},
  {"vaddr": "10037eae0", "want": "68020052", "new": "28008052", "desc": "refresh1"}
]
```

- `vaddr` —— 目标指令的虚拟地址（十六进制，无 `0x`）
- `want` —— **必须匹配**的原始 4 字节机器码（小端 hex）；不匹配则跳过该点
- `new` —— 替换后的 4 字节机器码
- `desc` —— 仅注释用

> 文件偏移由脚本按 `vaddr - UL_IMAGE_BASE` 推导，无需手工换算。

---

## 本地验证

```sh
# 1) 生成特征
UL_APP_NAME=... UL_BIN_NAME=... UL_BUNDLE_ID=... \
UL_PATCHES='[...]' python3 ci/gen_src.py

# 2) 构建
CC=clang LD=ld64.lld sh ci/build.sh

# 3) 端到端演练（把 postinst/postrm 跑在伪造的设备文件树上）
sh ci/e2e_test.sh <未改动的主二进制> dist/xxx-rootless.deb
```

`ci/e2e_test.sh` 会依次验证：改写命中数、`entitlements` 保留、
幂等性（重复执行不破坏）、以及 `postrm` 还原后补丁点逐字节回退。

---

## 入口段验收（每次交付必做）

现代 dyld4 读 `__TEXT,__init_offsets`；老式 Substrate / ElleKit 的初始化
扫描器只认 `__DATA_CONST,__mod_init_func` 里的函数指针。两者都要有：

```sh
llvm-otool -l x.dylib | grep -A3 __mod_init_func  # 必须存在，size=0x8
llvm-otool -l x.dylib | grep -c __init_offsets    # >= 1
llvm-otool -l x.dylib | grep -c LC_DYLD_INFO      # 必须为 0
llvm-otool -l x.dylib | grep -c LC_DYLD_CHAINED_FIXUPS  # 必须为 1
```

补充两点实测结论：

- 链接参数用 `-fixup_chains`（**不要** `-no_fixup_chains`）——
  后者会让 `ld64.lld` 产出畸形绑定流，dyld4 在 `prepare()` 阶段直接 halt。
- 想让 `ld64.lld` 在 `-fixup_chains` 下产出 `__mod_init_func`，
  段声明**必须写 `__DATA_CONST`**；写 `__DATA` 时该段根本不会生成。

---

## 已知限制

- `ldid -S` **不带参数**会抹掉 entitlements（云端同步能力会失效）。
  必须显式传 entitlement 文件；`postinst` 用 `ldid -e` 从原始备份现取。
- 没有 `ldid` 时 `postinst` 会**完全跳过改写**（改签缺失 → 签名失效 → 启动闪退），
  此时只剩 dylib 层兜底。
- 注入版 IPA 为 ad-hoc 签名，需用 TrollStore 或自签工具再签一次才能安装。
- DEB 与 IPA **不要同时安装**，会互相干扰。

---

## License

MIT
