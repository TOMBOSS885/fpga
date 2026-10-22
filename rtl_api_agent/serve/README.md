# 服务部署

本目录提供两层协议：`start.sh` 启动比赛投题服务；模型服务由官方基础环境或你自己的 AMD 主机运行，agent 通过 `LLM_BASE_URL` 访问。两者不是同一个端口。

```text
投题机 → /v1/solve :7860 → agent → /v1/chat/completions → AMD 模型
                                 ↘ xvlog → xelab → xsim → Vivado
baseline 模式 → 原版 baseline.py → 鉴权转发 → 同一个 AMD 模型
```

## 配置顺序

1. 在目标 AMD 环境中准备支持相应 gfx 架构的推理后端、开源权重和 chat template。正式赛所用后端、模型和依赖须在断网前落盘。
2. 启动一个专用模型实例，记录其完整命令、版本和权重摘要。设置相同的模型、量化、上下文和默认 reasoning 配置供两种模式使用。
3. 用 `env.example` 配置客户端，运行 `python3 -B -m serve.check_model`，再跑一题 `run.sh` 和 `run_baseline.sh`。
4. 在实际模型主机测量显存；启动 `serve/start.sh` 后通过鉴权的 `/v1/health` 检查就绪。
5. 在官方环境使用原有 selftest 判定完整产物，不以内部 `result.json` 作为正式分数。

不要把未经测试的启动命令当成通用 AMD 配方。W7900、R9700、Instinct 的 gfx 和后端 wheel/kernel 支持不同，本包不安装、升级或替换这些组件，也没有声称已完成任何型号的后端实机验证。

以下是已安装后端时的配置形状，`<...>` 必须替换，不能原样执行：

```text
vllm serve <local-model-path> --served-model-name <served-id> \
  --host 127.0.0.1 --port 8000 --max-model-len <actual-context> \
  --tensor-parallel-size 1
```

其他后端按其官方文档启动 OpenAI 兼容接口即可，不需要替换 agent。远程开发服务的鉴权/TLS 由模型主机配置，本包读取已有的 key 和 CA，不在命令行或 trace 中打印 key。远程调用的网络时延算入整题预算。

## 基线转发器

`serve/baseline.py` 启动一个临时的 `127.0.0.1` HTTP 转发器，执行官方 `baseline.py` 后清理。它只允许模型列表和 Chat Completions 路径；转发生成请求原始字节，不更改消息、温度、top_p、max_tokens，也不重试请求。官方基线自己的 300 秒请求上限保留，只受更短的整题截止时间约束。

转发器不调用 agent、skills、EDA 或 MCP。agent 与 baseline 都使用相同的远程模型配置。若未来官方基线自身支持鉴权，应更新固定基线来源与摘要验证，再简化这一传输层。

## 就绪与显存

`/v1/health` 同时检查服务端第一个模型 ID、一次真实短生成、Vivado 可执行文件和当前 AMD 显存。结果缓存 10 秒。没有真实指标时 `ready=false`，可通过附加的 `model_responding`、`missing_tools`、`vram_measured` 排查。

`measure_vram.py` 可在 Linux 模型主机读取 amdgpu 的 sysfs 计数；`--watch` 每 10 秒原子写入一次 JSON。远程主机使用 `VRAM_METRICS_FILE` 指向经可信方式同步/挂载的同一份测量。健康检查接受不超过 60 秒的记录和 0–32 GiB 当前占用；它不能证明峰值符合单卡预算，也不能代替官方审核。

## 官方资料

- [vLLM Online Serving](https://docs.vllm.ai/en/stable/serving/online_serving/)
- [SGLang OpenAI Compatible APIs](https://docs.sglang.io/docs/basic_usage/openai_api)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- 比赛接口以仓库 `rtlagent2026/docs/API_CONTRACT.md` 和最新赛事公告为准。
