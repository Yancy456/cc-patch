# Auto mode 分类器

这份说明对应本机 Claude Code **2.1.318**。分类器只负责 auto mode 里「这次工具调用能不能自动放行」。它不是主对话模型，也不是账号封禁器。

主对话和分类器是两次独立的模型请求。分类器看不到主对话的系统提示词，主对话也看不到分类器的规则。

## 使用的模型

默认是 **`claude-sonnet-5`**。请求上的 beta 头 `auto-mode-classifier-2026-07-16` 是接口版本，不是模型名。

`YEn()` 按这个顺序选模型：

1. 服务器配置 `tengu_auto_mode_config` 的 `model`，或按当前主模型匹配的 `modelByMainModel`。有这项时，环境变量被忽略。
2. 环境变量 `CLAUDE_CODE_AUTO_MODE_MODEL`。组织策略不允许，或模型是 `claude-sonnet-5-5`、`claude-opus-5`、`claude-opus-5-5` 时不用它。后三个会被接口的升级检查拒绝。
3. 内置默认 `pG()`。主会话是 `claude-sonnet-4-5` 或 `claude-sonnet-4-6` 时，分类器用主会话模型。其他主模型用 `claude-sonnet-5`。若 `ANTHROPIC_DEFAULT_SONNET_MODEL` 可用，且不是上面三个被拒绝的型号，就用那个值。
4. `claude-sonnet-5` 对这个账号不可用时，退回主会话模型。主会话是 Fable（`claude-fable-`）或 Mythos（`claude-mythos-`）时，改用 Opus，默认 `claude-opus-5`，或 `ANTHROPIC_DEFAULT_OPUS_MODEL`。

主会话开了 1M 上下文、而分类器模型本身不是 1M 时，模型 id 后面会加上 `[1m]`。

## 系统提示词

分类器请求带 `skipSystemPromptPrefix`，所以不会加上主对话那句 “You are Claude Code…”。提示词由三块拼成：

1. **框架**在 `v9r()` 里。身份是安全监视器：监视一个有 shell、文件系统和 API 凭证的自主编程代理。要拦的是人类开发者也不该擅自做的事，以及超出任务范围的事。三类风险是提示注入、任务越界、误伤。默认允许。含糊的「好」只覆盖助手刚才明确提议的那一步；助手自己的话不算同意；工具输出是数据，不是指示。
2. **规则正文**在 `permissions_external-2ede28fe.txt.zst`，解压后约 9.5 万字符。
   - **Environment**：组织、仓库是否公开、可信域名和桶。默认几乎都是「未配置」。
   - **HARD BLOCK**：用户意图也不能解开。默认主要是 Data Exfiltration。
   - **SOFT BLOCK**：破坏性或难撤回的操作，大约 71 条。用户必须点名这个动作和对象才能解开。
   - **ALLOW**：例外，例如只读、本地操作、已声明的依赖、推送到已配置的可信远程。
3. **用户规则**来自设置里的 `autoMode.allow`、`soft_deny`、`hard_deny`、`environment`。看起来像在给审查模型下命令、而不是在规定工具行为的行，会被丢掉。

输出只有两种：

- 拦截：`<block>yes</block>`，再带规则名和一句理由
- 放行：`<block>no</block>`

通常分两段，共用同一份系统提示和同一段 transcript。第一段不看用户是否同意，也不套 ALLOW 例外，回复在 `</block>` 处截断。第一段判拦之后，第二段才用用户原话决定能不能放开。服务器直接拒绝，或记录太长，结果按拦住处理。

## 上下文怎么组装

```mermaid
flowchart TB
  subgraph main["主对话请求 · 当前会话模型"]
    direction TB
    M0["请求头和 metadata<br/>账号、device_id、会话 ID"]
    M1["system ① 归因块<br/>版本、计费指纹"]
    M2["system ② 身份句<br/>You are Claude Code, Anthropic's official CLI for Claude."]
    M3["system ③ 行为说明<br/>Doing tasks · Using your tools · Tone and style"]
    M4["system ④ 会话材料<br/>CLAUDE.md、记忆、当轮环境"]
    M5["tools<br/>工具的名字、参数说明"]
    M6["messages<br/>用户话、助手话、tool_use、tool_result<br/>按时间排成真实多轮"]
    M0 --> M1 --> M2 --> M3 --> M4 --> M5 --> M6
  end

  subgraph cls["分类器请求 · 默认 claude-sonnet-5"]
    direction TB
    C0["同一套账号 metadata<br/>来源标记 auto_mode"]
    C1["system ① 归因块<br/>forceAttributionHeader"]
    C2["system ② 安全监视提示词<br/>威胁模型、同意门槛、输出格式"]
    C3["system ③ 规则<br/>Environment + HARD / SOFT / ALLOW<br/>再拼上 autoMode 自定义规则"]
    C4["system ④ 可选的 Session Context"]
    C5["messages 只有一条 user<br/>transcript 标签里是渲染后的对话<br/>要评的是其中最后一次工具调用"]
    C6["不带 tools<br/>模型只回复 block 为 yes 或 no"]
    C0 --> C1 --> C2 --> C3 --> C4 --> C5 --> C6
  end

  HIST["本机对话记录"] --> M6
  HIST --> C5
  main -.->|"某次工具调用要自动放行时<br/>另开这一次请求"| cls
```

主对话的 `system` 由 `Dos()` 组装：先放归因块，再放 “You are Claude Code…”，然后才是行为说明和会话材料。

分类器由 `cxr()` 生成自己的系统块。对话不按原始 `messages` 传递，而是收成一段文字，放进 `<transcript>`。只评这段文字里最后那次工具调用。上面的 `{"meta":…}` 是程序插入的事实，不算模型要执行的动作。
