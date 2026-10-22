# serve/

推理服务与投题接口。两者跑在同一个实例里，互不干扰。

```
  8000   vllm.sh        推理服务   绑 127.0.0.1，不暴露
  7860   serve_api.py   投题接口   绑 127.0.0.1，经 rc-tunnel 对外
```

---

## 开发期：不需要 GPU

```bash
export LLM_BACKEND=openai
export LLM_BASE_URL=https://api.deepseek.com/v1     # 或任意 OpenAI 兼容端点
export LLM_API_KEY=sk-xxxxxxxx
export LLM_MODEL=deepseek-coder
```

无需启动 `vllm.sh`，`agent/llm.py` 将直接访问所配置的端点。

## 正式提交：必须是本地权重

```bash
MODEL=/models/Qwen3-8B ./serve/vllm.sh &

export LLM_BACKEND=openai
export LLM_BASE_URL=http://localhost:8000/v1        # 换成本机
export LLM_API_KEY=dummy
export LLM_MODEL=Qwen3-8B
```

智能体代码无需改动，差异仅在于三个环境变量。但提示词与重试策略未必可以直接平移，商用
API 与本地小模型在输出长度、指令遵循、代码格式稳定性上差异明显，围绕前者调好的东西
换到后者常常直接失效。尽早跑一次本地模型。

## 起投题接口

```bash
export FPGACHINA_TOKEN=<赛事方发的令牌>
export MODEL_NAME=Qwen3-8B
uvicorn serve.serve_api:app --host 127.0.0.1 --port 7860

# 让外面能访问到：平台只接管绑在 127.0.0.1 上的 HTTP 服务
/var/run/secrets/frp-self-service/install
"$HOME/.local/bin/rc-tunnel" expose --port 7860
```

自测：

```bash
curl -H "Authorization: Bearer $FPGACHINA_TOKEN" http://localhost:7860/v1/health
# {"ready":true,"track":"rtl","model":"Qwen3-8B","vram_gb":21.4}
```

`track` 必须是 `"rtl"`，赛事方投题前会核对，不符即拒投。

---

## 几处容易踩的地方

**绑 `127.0.0.1`，不是 `0.0.0.0`。** Radeon Cloud 的 `rc-tunnel` 只接管绑在环回口上的
HTTP 服务，绑 `0.0.0.0` 平台不接管。绑错了服务本身是活的，但外面永远打不进来，在赛事方
那边看起来和挂掉没有区别，而失败不重投。

**只暴露 7860 一个端口。** 平台限制一个 notebook 同时只能暴露一个端口。vLLM 的 8000
由智能体经 `127.0.0.1` 内部调用，不要去暴露。**同时参加两个赛道的队伍需要两台 VM。**

**令牌检查不是可选项。** 平台给出的公网 app URL 不带业务层鉴权，没有令牌检查，任何人
拿到网址都能投题。

**工程与中间文件落本地盘。** `AGENT_TMP` 默认 `/tmp`。云平台的 `/workspace` 是 NFS，
Vivado 综合产生的大量小文件写在上面极慢。这不是优化建议。

**服务要能连续跑数百次请求不重启。** 评测期间挂了就是 L0，赛事方不重投。注意每题之后
清理临时目录与子进程，别把磁盘写满。

**自己先超时，别让赛事方超时。** `serve_api.py` 把子进程超时设为 `deadline_s - 5`，
到点回收并返回已有的最好结果。赛事方侧超时一定是 L0，自己先停至少还有 L1/L2 的可能。

---

## 跨代架构的已知问题

验证阶段的 gfx1100（W7900）与决赛的 gfx1201（R9700）是两代架构，预编译内核与 wheel
不通用。基础镜像里已给出可用配置，但以下几条应当知悉：

1. **跨卡张量并行与流水并行在 gfx1201 上不可用**，无 XGMI，集合通信库无法可靠启动。
   四卡的推荐用法是每卡一个独立实例、四路并发。
2. **FP8 存在「硬件有、软件未必用得上」的情况。** gfx1201 具备 FP8 E4M3 矩阵指令，
   但部分框架缺少该架构的平台检测，会将 FP8 权重静默反量化为 FP32，导致算力浪费，
   而且没有任何告警。
3. **部分算子库在 RDNA 4 上不工作**，须显式关闭。
4. **在 gfx1100 上做 FP8 量化只省显存、不获加速**，该架构无 FP8 矩阵指令。验证阶段
   测到的量化收益不能直接外推到决赛。
