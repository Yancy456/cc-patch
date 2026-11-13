# Claude Code 完全逆向报告和防止封禁指北

**TLDR：**

1. **Claude Code开发语言和工具**
   - TypeScript + React/Ink，经 Bun 打成字节码嵌入
   - 发布物是 Bun standalone ELF：`.text`≈110 MiB 运行时 + `.bun`≈180 MiB 应用 JS
2. **会上报什么**
   - 第一方遥测批次（可关）：`device_id`（即本地 `~/.claude.json` 的 `userID`，两者一样）、账号/组织 UUID、会话、平台/发行版/内核/终端/shell 等
   - 每次模型请求（关不掉）：`metadata.user_id` 里的 `device_id`（同上，等于 `userID`） / `account_uuid` / `session_id`，以及 `User-Agent`、`X-Stainless-OS` 等头
3. **防封禁要干什么**
   - 关遥测：`DISABLE_TELEMETRY` / `DO_NOT_TRACK` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
   - 设备类型伪装：对外伪装成 darwin/MacOS（Linux 封禁概率最高）
   - 重置 `userID`（即上报的 `device_id`，两者一样），不要和封禁账号使用同一个
4. **防止机器学习封禁（主要！！）**
   - 账号 UUID、订阅身份、请求行为模式仍在，换 `device_id`（`userID`）挡不住行为侧风控
   - 别频繁换 ID：短时间反复重置 + 异常用量，本身就容易被当成异常样本
   - **中文交互、高频使用时段与注册地区不符，也可能触发风控**
   - **色情内容、逆向内容、网络攻击（爬虫）等不合规操作非常容易被封禁**（作者亲身验证，被封两号）

---

## 一、分析环境与工具链

| 项目 | 版本 / 说明 |
|---|---|
| Claude Code | **2.1.318**（`@anthropic-ai/claude-code`） |
| 服务器系统 | **Ubuntu 22.04.5 LTS**，内核 5.15.0-91-generic，x86_64 |
| 安装形态 | Bun 打出来的 Linux ELF（文件名叫 `claude.exe`，头是 `\x7fELF`），约 322MB |
| 逆向工具 | **REA**（`rea-agents` **6.8.0**）+ Grok 4.7 |

关键步骤：

1. `inspect-with-objdump section_headers` 定位嵌入脚本节（等价 `objdump -h` / `readelf -S`）
2. 从 ELF 的 `.bun` 节按 Bun standalone 布局切出 JS 模块（REA 解不开整张模块图，这一步自写）
3. 遥测相关 chunk 交给 `analyze-javascript-application`
4. 字段表直接读压缩函数，再和 `~/.claude/telemetry/1p_failed_events.*.json` 对照（当时 6 个文件 / 1368 条事件 / 103 个事件名）

---

## 二、执行文件组成结构

### 2.1 Bun 是什么

**Bun** 是一个用 Zig 写的 JavaScript/TypeScript 运行时（兼打包器、测试跑手、包管理器），定位接近 Node.js，但自带更快的启动和一体化工具链。引擎底层是 **JavaScriptCore**（WebKit 那套），不是 V8。

Claude Code 发布形态用的是 Bun 的 **standalone**：把 Bun 运行时和业务脚本打进**同一个可执行文件**。所以本机的 `claude.exe` 名义上叫 exe，头却是 `\x7fELF`——跑起来是 Bun 在本地执行嵌入的 JS，而不是再拉起系统里的 `node`。崩溃信封里也能看到硬编码的 `bun_version:"1.4.4"`。

npm 包装只负责选平台：`install.cjs` 从 `optionalDependencies` 里拷对应的原生二进制盖到 `bin/claude.exe`，之后 `claude` 直接 exec 这个 ELF，**没有常驻 Node 进程**。

### 2.2 节表与布局

安装包入口是 npm 的 `bin/claude.exe`（`package.json` 里 `"claude": "bin/claude.exe"`）。文件本身是 **Bun standalone ELF**：外层是完整的 Bun 运行时，应用 JS 嵌在专用节里。

对本机 `claude.exe.backup`（337 814 032 字节 ≈ 322 MiB）跑 `readelf -S`，主要块如下：

```
节名              文件偏移        大小           占比     作用
.note.bun         0x390           ~4 KiB         —        Bun 版本 NOTE
.bun_trim         0x16aa0         ~115 KiB       —        Bun trim 元数据
.rodata           0x34000         ~26.8 MiB      8.3%     只读数据
.bun_aot          0x1af92b8       ~4 KiB         —        AOT 相关
.bun_features     0x1afa158       ~0.5 KiB       —        feature 位图
.bun_builtins     0x1afa370       ~2.6 MiB       0.8%     Bun 内置模块
.text             0x1dd1a00       ~110.5 MiB     34.3%    Bun/JS 引擎机器码
.bun              0x8e77000       ~179.6 MiB     55.8%    嵌入的 CC 应用包
```

### 2.3 开发语言与工具链（推测）

Anthropic 没开源 CC 主仓构建脚本；下面是按 **2.1.318 二进制 + npm 包装** 反推的栈，置信度标在括号里。

| 层级 | 推测 | 依据 |
|---|---|---|
| 应用主语言 | **TypeScript → 编译/打包成 JS**（高） | `.bun` 模块头带 `// Version: 2.1.318`；minify 后仍是标准 ESM/`import`；npm 附带 `sdk-tools.d.ts`（`json-schema-to-typescript` 生成）；包装 `engines.node >= 22` |
| 终端 UI | **React + Ink 一类 TUI**（中高） | 二进制里大量 `react` / `jsx-runtime` / `useStdout` / `<Box` 等 Ink 常见符号；CLI 交互形态与 Ink 一致 |
| HTTP / 模型客户端 | **Anthropic 官方 JS SDK（Stainless 生成）**（高） | `Anthropic/JS`、`X-Stainless-OS` / `X-Stainless-Arch` / `X-Stainless-Runtime` 头；字符串 `@anthropic-ai/sdk` |
| 打包 / 发布 | **Bun compile → 分平台 standalone**（高） | ELF 含 `.bun` / `.note.bun` / `.bun_builtins`；约 2859 处 `// @bun @bytecode`；`optionalDependencies` 按 darwin/linux/win32 × arch 拆包 |
| 运行时（嵌入） | **Bun 1.4.4 + JavaScriptCore**（高） | `bun_version:"1.4.4"`；节名与 JSC/WTF 数据节；Bun 本身用 Zig 写，但那是运行时实现语言，不是 CC 业务语言 |
| 安装包装 | **少量 Node CJS**（确定） | 根包只有 `install.cjs` / `cli-wrapper.cjs`，postinstall 用 `node` 选原生二进制 |

**不太像**的：用 Rust/Go/Swift 写主业务再链进这个包——主逻辑几乎全在 `.bun` 的 JS/字节码里；`.text` 主要是 Bun 引擎，不是业务手工写的原生代码。源码侧很大概率是 **TS + React/Ink**，用 **Bun** 做开发/打包，再按平台吐出 standalone；对外仍走 npm 分发。

---

## 三、核心结论：两条上报通道

**关掉遥测只停第一条。第二条（模型请求）照样带身份。**

### 3.1 第一方遥测（可关）

- 地址：`https://api.anthropic.com/api/event_logging/v2/batch`
- 事件类型：`ClaudeCodeInternalEvent`

开关逻辑在嵌入 JS 里（函数名经 minify，行为不变）：

```javascript
function Yin(){
  if(process.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC) return "essential-traffic";
  if(process.env.DISABLE_TELEMETRY) return "no-telemetry";
  if(Oe(process.env.DO_NOT_TRACK)) return "no-telemetry";
  return "default";
}
function EU(){ return Yin() !== "default"; }
```

另一处把同一组环境变量收成布尔门闩：

```javascript
function ne(e,t){
  let r = e.nodeEnv==="test"
        || s(e.disableTelemetry)
        || s(e.disableNonessentialTraffic)
        || m(e.doNotTrack)
        /* …网关 / Bedrock / Vertex 等也会短路… */;
  return r || /* … */;
}
```

因此下列任一环境变量有值即停发第一方遥测批次：

- `DISABLE_TELEMETRY`
- `DO_NOT_TRACK`
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`

公共字段（节选，与失败队列 JSON 形状一致）：

| 字段 | 含义 | 代码依据 |
|---|---|---|
| `device_id` | 64 位 hex，来自 `~/.claude.json` 的 `userID` | `kN()` → `wc(32).toString("hex")` 写入 `userID` |
| `auth.account_uuid` / `organization_uuid` | 账号与组织 | 事件信封里的 `auth:{account_uuid, organization_uuid}` |
| `session_id` / `event_id` / `client_timestamp` | 会话与时间 | 同名字段序列化进 `ClaudeCodeInternalEvent` |
| `env` | 平台、发行版、内核、终端、shell、版本等 | `XY()` 组装；含 `linux_distro_id` / `linux_kernel` |
| `process` | Base64 JSON：RSS、堆、CPU、uptime | 进程采样字段 |
| `head_sha` | Git HEAD（有条件） | 非 diskless + OAuth + Pro/Max + SHA 非空 |

`env` 组装（Linux 默认 `platformRaw:"linux"`，补丁改的就是这里）：

```javascript
return {
  platform: jKe(),
  platformRaw: process.env.CLAUDE_CODE_HOST_PLATFORM || "linux",
  arch: i.arch,
  nodeVersion: i.nodeVersion,
  terminal: e ? null : aR.terminal,
  shell: e ? "none" : jko(),
  /* … */
};
```

发行版信息只在 `platform === "linux"` 时读取：

```javascript
async readLinuxDistroInfo(){
  if(this.sources.platform !== "linux") return;
  let e = { linuxKernel: this.sources.osRelease() };
  /* 读 /etc/os-release → linux_distro_id / linux_distro_version */
}
```

### 3.2 每次模型请求（关不掉）

`metadata.user_id` 由 `f3e()` 生成。函数体**不读** `Yin()` / `DISABLE_TELEMETRY`，只拼身份 JSON：

```javascript
function f3e({agentContext:e, servedSessionMode:n} = {}){
  /* … CLAUDE_CODE_EXTRA_METADATA 可选合并 … */
  let w = {
    device_id: kN(),
    account_uuid: /* OAuth / 配置里的 accountUuid */ "",
    session_id: G(),
    ...h && { parent_session_id: h },
  };
  /* … 超长则回退 … */
  return { user_id: _(S) };  // _(·) = JSON.stringify
}
```

因此请求体里的 `metadata.user_id` 始终带：

- `device_id`（同上，`kN()`）
- `account_uuid`
- `session_id`
- 可选 `parent_session_id`

HTTP 头还会带（字符串表与 SDK 头构造均在 `.bun` 内）：

| 头 | 内容 | 代码依据 |
|---|---|---|
| `User-Agent` | `claude-cli/<版本>` + 入口名 | 头名字面量 |
| `x-app` | `cli` / `cli-bg` | 同左 |
| `X-Claude-Code-Session-Id` | 会话 ID | 同左 |
| `X-Stainless-OS` | 未补丁时 Linux → `"Linux"` | `if(e==="linux")return"Linux"` |
| `X-Stainless-Arch` | 本机 `x64` | `X-Stainless-Arch` |
| `X-Stainless-Runtime` | `node` + 版本 | 同左 |

对话正文、工具参数走推理接口，不是遥测批次。更细的环境头挂在功能开关 `tengu_wiggly_dove` 上，**默认关**。

---

## 四、修复补丁参考

本仓库 `claude_patch.py` 对 `claude.exe` 做等长原地字符串替换（Bun 模块表偏移不用改）。`process.platform` 仍是 `linux`，避免客户端去 macOS 路径找工具；只改**对外上报字符串**。

`patch` 还会：备份原版到本目录 `claude.exe.backup`、重置 `~/.claude.json` 的 `userID`（即 `device_id`）、把遥测三变量写入 `~/.claude/settings.json`。

补丁锚点与二进制原文一一对应（均在 `.bun`）：

| 上报项 | 原文锚点 → 补丁后 |
|---|---|
| `X-Stainless-OS` | `if(e==="linux")return"Linux"` → `"MacOS"` |
| `platform` / `jKe()` 回落 | `return c.platform` → `return"darwin"\|\|o` |
| `platformRaw` 默认 | `\|\|"linux",arch:` → `\|\|  jKe(),arch:` |
| 遥测 `shell` | `shell:e?"none":jko()` → `"zsh"` |
| 遥测 `terminal` | `terminal:e?null:aR.terminal` → `"iTerm.app"` |
| `linux_distro_*` | `platform!=="linux"` → `platform!=="never"`（跳过读发行版） |

**刻意不改的：** `arch`（保持 `x64`）、Node/Bun/包管理器、账号 UUID、会话 ID。  
`arch` 乱改会导致客户端去拉错架构的工具。

```bash
python3 claude_patch.py patch    # 备份 + 打补丁 + 重置 device id + 关遥测
python3 claude_patch.py restore  # 从本目录备份还原二进制
```

补丁只改对外字符串与本地 `userID`，**无法防止机器学习封禁**（账号行为、不合规内容、用量模式等仍会被风控）。

---

## 五、常见提问

### 会上报时区吗？

按 2026-11-12 本机配置：普通对话**不上传时区名**。  
只有打开 `CLAUDE_CODE_PER_TURN_TIMING` 且模型支持 `timing-2026-09-09` 时，才会带数字偏移的当地时间。  
主提示里只有 `Today's date is YYYY-MM-DD.`，没有 `Asia/Shanghai`。

### Claude Code 系统提示词是什么？

见 [`claude_code_system_prompt.txt`](./claude_code_system_prompt.txt)（身份句、`# System` / `# Doing tasks` 等经典段落、以及 `# Harness` 精简布局）。分类器提示词另见 [`classifier.md`](./classifier.md)。

https://github.com/Yancy456/claude_reverse
