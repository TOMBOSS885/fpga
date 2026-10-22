# RTL API Agent

这是一个独立的 **RTL 赛道**方案包，入口与官方参考结构一致。模型通过标准 HTTP API 提供服务，agent 在具备 Vivado 的机器上生成、检查和修复 SystemVerilog。现有 `rtlagent2026/agent` 和官方 `example` 都没有被覆盖。

当前完成了工程实现和本地协议回归，**还没有在真实 AMD 模型和官方 Vivado 环境上测出通过率**。模型尚未部署，因此 `MODEL.md`、`manifest.json` 中的实际模型信息仍需要填写。

## 目录

```text
rtl_api_agent/
├── Dockerfile
├── model/MODEL.md
├── agent/                 # API、RTL 处理、工具循环、预算、轨迹
├── skill/                 # 官方接口技能 + 现有团队 RTL 技能
├── serve/                 # 服务端点、模型连通检查、基线鉴权转发
├── run.sh
├── run_baseline.sh
├── baseline.py            # 官方原文件，字节不变
├── manifest.json
├── REPORT.md
└── tests/                 # 本地协议回归及可选 Vivado 测试
```

## 连接模型 API

在本目录执行。远程模型服务须提供 `/v1/models` 和 `/v1/chat/completions`，配置的模型名必须与 `/models` 返回的 ID 一致。当前采用同一个模型完成所有角色，agent 与 baseline 也使用这个模型，避免破坏增益对照。

```bash
cp serve/env.example .env
# 编辑 .env：填写地址、模型名和实际上下文长度
set -a
source .env
set +a
python3 -B -m serve.check_model
```

例如远程 AMD 模型服务已经启动在 `http://amd-host:8000/v1` 时：

```bash
export LLM_BASE_URL=http://amd-host:8000/v1
export LLM_MODEL=实际服务的模型ID
export MODEL_NAME="$LLM_MODEL"
export LLM_CONTEXT_TOKENS=16384
# 需要鉴权时，在外部创建的密钥文件中存放原始 key：
export LLM_API_KEY_FILE=/run/secrets/model_api_key
python3 -B -m serve.check_model
```

`OPENAI_BASE_URL`、`OPENAI_API_KEY` 可作为别名，优先使用 `LLM_*`。HTTPS 默认验证证书，私有 CA 用 `LLM_CA_BUNDLE`，不提供关闭 TLS 验证的默认路径。

| 推理服务 | 常见 Base URL 示例 | 本包使用方式 |
| --- | --- | --- |
| vLLM | `http://host:8000/v1` | 标准 Chat Completions |
| SGLang | `http://host:30000/v1` | 相同协议 |
| llama.cpp server | `http://host:8080/v1` | 相同协议，使用服务端模型 ID |
| Ollama | `http://host:11434/v1` | 使用 `/v1` 兼容接口，不调用 `/api/chat` |

这些是协议适配目标和常见地址，不是已经完成的后端实机验证。模型须支持 chat template。默认 `stream=false`；可用 `LLM_STREAM=1` 测试 SSE，但基线仍按官方脚本的固定请求执行。首轮接入建议先用非流式。

## 终端可视化入口

开发调试可以使用纯标准库的终端面板。它针对 Verilog/SystemVerilog 任务，直接调用同一份 `run.sh` 工作流，不改变官方提交入口。面板支持编辑模型端点、模型 ID、上下文/输出上限、修复预算、`xczu3eg-sbva484-1-e` 器件、5 ns 时钟和 Vivado 路径；运行中会增量显示 `trace.jsonl` 的模型、`xvlog`、`xelab`、`xsim`、综合和 checkpoint 事件。

```bash
cd rtl_api_agent
./terminal_agent.sh --input ../rtlagent2026/tasks/ex01_popcount8 --output /tmp/rtl-ui
```

面板按 `r` 开始运行，上下键滚动选择全部配置，`e` 或回车编辑当前行，`s` 保存 `.rtl-agent.json`，`d` 刷新 Vivado 状态，`q` 退出（运行中不会误停任务）。80×24 窗口采用上下布局，宽窗口分栏。当前阶段、剩余预算、RTL 风险、所选技能和最近故障会单独显示；本地等级明确标注为非官方成绩。编译、仿真、综合预算可分别配置为 `AGENT_LINT_S`、`AGENT_SIM_S`、`AGENT_SYNTH_S`，均受单题总预算约束。也可以完全脚本化：

```bash
./terminal_agent.sh --no-ui \
  --input ../rtlagent2026/tasks/ex01_popcount8 --output /tmp/rtl-cli \
  --set LLM_BASE_URL=http://127.0.0.1:8000/v1 \
  --set LLM_MODEL=your-local-model
./terminal_agent.sh --doctor --no-ui
```

`--doctor` 检查当前 `vivado`、`xvlog`、`xelab`、`xsim` 的路径、版本和运行进程，在临时目录启动版本探测，不打开 GUI 或修改工程。“找到工具”不代表许可证、实际仿真和综合通过。工具发现顺序为 `VIVADO_BIN`、`XILINX_VIVADO/bin`、已有 `PATH`、本机及官方镜像常见路径；显式配置错误不会悄悄切换到另一安装版本。API key 只从现有环境变量/密钥文件继承，终端配置文件不会保存它，也拒绝在 URL 中保存凭据。

运行输出保存在所选目录中，`console.log` 记录面板启动的脚本输出。每次运行使用独立 nonce 读取当前轨迹，复用输出目录时不会把上一次的结束状态当成这一次的结果。

## Verilog 专项策略

参考本地 OpenCode 的工具事件与状态显示方式，保留单模型、有界控制流。原题始终完整保留；根据端口和题面识别位宽/有符号、时钟复位、FSM、序列、握手和存储器等风险，按阶段选择最多三个技能，并为独立自测列出适用检查点。详见 [skill/README.md](skill/README.md)。

技能模板服从原题：保留规定的下降沿、锁存器、初始化、控制优先级、LFSR 种子和输出延迟。接口/语法、仿真、综合分别进入对应修复；许可证、运行库和工具崩溃停止代码修复。综合要求实际输出完成标记，后续失败修改不能覆盖已达到更高验证等级的 RTL。

agent 默认输出上限 4096 tokens；官方基线固定请求 8192 tokens。服务端上下文应给题面、系统提示与这些输出留足空间，两种模式必须共用同一份后端上下文配置。客户端的 token 估计不是精确 tokenizer，复杂题不要把上下文配置压到边界。

## 文件入口与产物

```bash
./run.sh ../rtlagent2026/tasks/ex01_popcount8 /tmp/rtl-agent-one
./run_baseline.sh ../rtlagent2026/tasks/ex01_popcount8 /tmp/rtl-baseline-one
```

只读取输入目录的 `prompt.txt` 和可选 `interface.txt`，不会读取 `ref.sv` 或 `tb.sv`。正式 RTL 输入的 `interface` 为空，端口从题面解析。

输出至少包含 `solution.v`、`trace.jsonl`。agent 另外写 `result.json`、`candidate.sv`、`testbench.sv` 和 `logs/`；其中的等级是模型自测产生的内部里程碑，**不是官方分数**。生成失败时返回空产物；已经编译通过的代码会及时保存，后续修复失败会回退。

baseline 使用原封不动的官方 `baseline.py`。由于该脚本没有 API Key 请求头，本包在运行它时启动一次临时 loopback 转发器，只追加上游鉴权头，原请求正文与采样参数保持不变；没有额外生成、重试、RTL 工具或技能注入。鉴权是否成功及单次调用性质都有回归测试。

## 提交 HTTP 服务

```bash
export FPGACHINA_TOKEN=赛事方分配的令牌
./serve/start.sh
```

服务绑定 `127.0.0.1:7860`，由官方平台的 `rc-tunnel` 转发；推理端口不通过投题隧道暴露。支持带 Bearer 鉴权的 `GET /v1/health`、`POST /v1/solve`，支持 `mode=agent|baseline`。一次处理一个解题请求，并发提交返回 503，避免多份 Vivado 任务争抢单卡与单题预算。

健康检查会实际访问模型列表并发送短请求，检查 Vivado 工具和实测显存。缺少这些条件时 `ready=false`，不会把“HTTP 服务活着”当作“可评测”。

同一 AMD 主机上，健康检查可直接读取 amdgpu sysfs。多 GPU 时使用 `GPU_DRM_CARD` 选择实际部署卡。若 agent 和模型不在同一台机器，在模型主机运行：

```bash
python3 -B -m serve.measure_vram --output /tmp/model-metrics/vram.json --watch
```

通过已有的可信文件同步/挂载让 agent 读取该文件，设置 `VRAM_METRICS_FILE`。测量记录超过 60 秒失效。它记录当前整张卡显存，不代表峰值显存、模型独占占用或单卡推理合规证明；正式报告仍需记录峰值、硬件和后端配置。

## 官方环境与容器

开发时可以访问远程 AMD API；正式断网评测应让模型运行在官方允许的环境中，把 `LLM_BASE_URL` 改成同环境内的地址。客户端可配置远程地址不代表正式赛允许出网。

使用赛事方公布的基础镜像标签构建，不在运行时下载工具、依赖或权重：

```bash
docker build --build-arg BASE_IMAGE=赛事方公布的镜像标签 -t rtl-api-agent .
```

继承基础镜像的 `PATH`、`XILINX_VIVADO`、`EDA_TMP`，不硬编码 Vivado 安装版本。临时 EDA 工程落在节点本地盘，每次调用隔离并清理。目标器件默认 `xczu3eg-sbva484-1-e`，时钟参数 5 ns。Python 需要 3.10+，运行时只用标准库。

容器不包含模型权重或已安装的 vLLM/SGLang。模型须按实际 AMD 卡和官方镜像准备并提前落盘；启动配置及记录方式见 [serve/README.md](serve/README.md)。`MODEL.md` 必须填真实版本、量化、显存、上下文，不可把示例配置当实测。

## 回归和外部评测

```bash
python3 -B -m unittest discover -s tests -v
# 本机确实已有 Vivado 后，再启用原生仿真/综合测试：
RTL_NATIVE_TESTS=1 python3 -B -m unittest discover -s tests -v
```

从比赛仓库外部调用原有官方判定器，不改官方实例：

```bash
../rtlagent2026/selftest/run_selftest.sh --agent "$(pwd)" --samples 5 \
  --out /tmp/rtl-api-official-selftest
```

后续使用 VerilogEval v2 `dataset_spec-to-rtl` 做独立外部判定，不把参考实现或测试台注入 agent。本包没有内置题目答案、确定性题型解法或 mock 模型。设计依据与已知限制见 [REPORT.md](REPORT.md)。
