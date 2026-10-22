# RTL 技能包

本包只服务 Verilog/SystemVerilog 赛道。技能补充实现和验证经验，不能替代题面、推断隐藏测试或携带题目答案。输入仅为原题和可选接口，不读取 `ref.sv`、`tb.sv`。

| 技能 | 适用风险 |
| --- | --- |
| rtl-module-interface-contract | 顶层、端口、方向、位宽；始终预留一个选择位置 |
| rtl-arithmetic-truncation | signed、扩展、截断、进位、popcount、移位 |
| rtl-clock-reset-conventions | 有效边沿、同步/异步复位、极性、使能 |
| rtl-control-priority-pattern | 同时有效的 reset、clear、load、enable |
| rtl-fsm-idioms | 状态编码、转换、Moore/Mealy 与输出周期 |
| rtl-shift-register-pattern | 序列重叠、窗口有效性、LFSR 抽头与种子 |
| rtl-synthesis-latch-prevention | 非预期 latch、多驱动、不支持的综合构造 |

选择器使用题面关键词、解析出的 RTL 风险、当前阶段和诊断，每轮最多选择三个，按字符预算分配上下文；综合修复优先加载综合技能。技能选择和风险信息写入轨迹。首次独立验证只给原题，不给 DUT。

所有模板服从题面：不得强制上升沿、复位最高优先级、非零种子或注册输出；保留指定的锁存器及 Vivado 支持的初始化。许可证、共享库错误、工具崩溃与墙钟超时交给环境诊断，停止代码修复。

官方接口技能来自 `rtlagent2026/example/skill/rtl-module-interface-contract`，保持原文件与 AMD 版权声明。其余六个技能基于团队原有 `rtlagent2026/skill`，本包在 2026-10-22 修订了可能覆盖题意的通用模板；原仓库和全局技能未修改。

回归已覆盖风险选择、综合诊断路由、环境故障停止与候选回退，格式校验覆盖七个技能。它们验证工程行为，不能证明技能提高模型通过率；真实增益须用同一模型、同一后端和上下文，对照原版单次生成基线，再由官方工具及独立参考判定。
