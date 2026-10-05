# API 开发启动脚本

## 一次配置

编辑本目录的 `api.local.json`，把 `api_key` 的空字符串替换为新密钥。
不要使用此前已暴露的密钥。该文件被 Git 忽略，但仍是本地明文文件，
不要分享、截图或打包上传。共享模板为 `api.example.json`，其中不应填写真实密钥。

学校网关地址和模型名沿用此前提供的配置，尚未验证服务器接口。
脚本只追加 `/chat/completions`，不会自动补 `/v1`。
若学校文档规定其他前缀，请修改 `base_url`。当前 HTTP 地址会明文传输密钥；
应优先使用学校提供的 HTTPS 地址，不关闭证书校验。

## WSL 中运行

```bash
cd /mnt/f/amd_rtl/rtlagent2026/example
python3 run_api.py --check
python3 run_api.py
```

`--check` 只校验本地配置，不发起网络请求，不证明 DNS、密钥或模型可用。
默认题目是 `ex03_lfsr8`，输出写入新的 `/tmp/rtl-api-*` 目录，实际路径会打印出来。
若缺少 `requests`，在当前 Python 虚拟环境中安装 `requirements.txt` 后再运行。

指定其他题目和新的输出目录：

```bash
python3 run_api.py ../tasks/ex01_popcount8 /tmp/popcount-api-test-01
```

已有非空输出目录不会覆盖，请换一个目录名。
不再需要手动 `export`，本地配置会覆盖子进程的同名环境变量，并固定使用
`LLM_BACKEND=openai`。这里的 `openai` 是兼容协议名称，不代表必须调用 OpenAI 的模型。

## 限制

- 仅供开发；不修改正式 `run.sh`、agent 实现或赛事基线。
- 默认只生成一轮，避免无效 Vivado 环境引发重复 API 消费；后续可改 `max_rounds`。
- DNS、校园网/VPN、API 额度和服务端错误仍需要单独排查。
- Vivado 由 agent 工具层自动选择 Linux 或 Windows 入口，详见下节。
- 退出码 0 仅表示非空 RTL 和 trace 文件已产生，不代表 L1/L2/L3 通过。
- 输出目录保存代码和 trace；失败时需检查 trace，不要公开其中可能包含的服务端敏感信息。

## WSL 调用 Windows Vivado

当前 WSL 的 PATH 已包含 `/mnt/f/Vivado/2022.2/bin`。
agent 会识别同目录下的 `vivado.bat`、`xvlog.bat`、`xelab.bat`，
通过 Windows PowerShell 启动 `cmd.exe` 调用这些入口，不再运行缺少 Linux 二进制的启动脚本。
原来的 `python3 run_api.py` 用法保持不变，不需要再次填写 API Key。

启动时会实际执行 `vivado.bat -version`，trace 的 `env` 事件记录：

```json
{"tool":"env","vivado_backend":"windows-wsl","vivado_version":"2022.2","development_only":true}
```

若自动识别失败，可在当前终端指定安装根目录（不是 `bin`）：

```bash
export VIVADO_BACKEND=windows
export VIVADO_WINDOWS_ROOT=/mnt/f/Vivado/2022.2
```

`VIVADO_BACKEND` 默认为 `auto`，也可设为 `linux` 强制使用 Linux 工具。
未来 Linux 服务器仍走原生调用，不需要安装 Windows PowerShell。

Windows 工具临时文件默认放在仓库根的 `.vivado-work/`，已被 Git 忽略；
每次检查使用独立目录，不会覆盖原始 `.v` 文件。
可用 `VIVADO_WINDOWS_TMP` 指定其他 Windows 盘目录，例如 `/mnt/f/eda-tmp`，
不能使用 WSL 的 `/tmp` 或 UNC 路径作为 Windows 工具工作目录。
原始输入和最终输出仍可放在 `/tmp`，agent 会复制待验证源码到 Windows 工作目录。
普通运行结束后清理工作目录；设置 `AGENT_KEEP_WORK=1` 可保留完整日志。
正常工具超时由 Windows 端终止本次 cmd 进程及其子进程，不按名称结束其他 Vivado 实例。

### 直接检查已有代码，不调用 API

```bash
python3 check_vivado.py /tmp/rtl-api-w48ozd93/solution.v
```

也可以传入其他 `.v` 路径。该脚本执行 `lint`（xvlog + xelab）和 `synth`，
保留工作目录、完整日志、综合成功时的 `synthesized.dcp`，并打印 `report.json` 路径。
支持 `--top TopModule` 和 `--timeout 300`（每项检查的秒数）。
不运行参考测试台，也不声称功能仿真或正式 L3 判定通过。
Vivado 2022.2 仅用于当前开发反馈，最终仍需按项目要求在 2026.1 复测。

实现范围仅为 agent 的编译和综合工具；官方 `selftest/judge/` 及基线没有修改，
不能据此认为官方自测脚本已支持 Windows。

### 本机验证记录（2026-09-28）

- 自动识别 `F:\Vivado\2022.2`，实际版本为 2022.2。
- 已生成的 LFSR RTL 通过 Windows `xvlog` 和 `xelab`。
- `get_parts` 确认当前安装中没有 `xczu3eg-sbva484-1-e`；默认综合会返回 `rc=-1`，
  提示目标器件不可用，agent 不会为此反复调用模型修代码。
- 临时指定本机已有的 `xc7z020clg400-1` 后，相同 RTL 综合成功并生成 DCP；
  仅验证 Windows 调用链路，没有更改默认器件，也不代表赛事综合通过。
- 路径含空格、非零退出码、超时后子进程树清理均有真实 Windows 进程测试。
- 没有调用付费 LLM API，没有运行功能测试台，没有修改基线或官方判定器。

要在默认器件上完成综合，需要为当前 Vivado 安装补齐对应器件支持；
正式验收仍应使用项目规定的 Vivado 2026.1 环境。
