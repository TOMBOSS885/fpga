# 本地真实模型评测与失败分析

模型：本机 Qwen2.5-Coder 7B Instruct Q4_K_M（Ollama）。显卡：用户RTX4060 Laptop 8GB；不是比赛AMD硬件。
同一模型服务、同一上下文服务配置；官方baseline原样使用。每模式每题只有一次采样，不能据此推广总体能力或保证获奖。
公开3题用于冒烟；6题为已知开发回归集，不是未见留出集。关闭skill仅改变技能加载，不关闭多角色与反馈。

## public

|题目|模式|外部判定|状态|
|---|---|---|---|
|ex01_popcount8|baseline|L3|OK|
|ex01_popcount8|agent|L3|OK|
|ex02_detect_1101|baseline|L1|FUNCTION_ERROR|
|ex02_detect_1101|agent|L3|OK|
|ex03_lfsr8|baseline|L3|OK|
|ex03_lfsr8|agent|L1|FUNCTION_ERROR|

## regression

|题目|模式|外部判定|状态|
|---|---|---|---|
|h01_alu|baseline|L0|CODE_ERROR|
|h01_alu|agent|L3|OK|
|h02_saturating_add|baseline|L1|FUNCTION_ERROR|
|h02_saturating_add|agent|L0|CODE_ERROR|
|h03_priority_counter|baseline|L1|FUNCTION_ERROR|
|h03_priority_counter|agent|L1|FUNCTION_ERROR|
|h04_enabled_pipeline|baseline|L1|FUNCTION_ERROR|
|h04_enabled_pipeline|agent|L1|FUNCTION_ERROR|
|h05_pattern_0110|baseline|L1|FUNCTION_ERROR|
|h05_pattern_0110|agent|L1|FUNCTION_ERROR|
|h06_pattern_1001001|baseline|L1|FUNCTION_ERROR|
|h06_pattern_1001001|agent|L1|FUNCTION_ERROR|

## public_no_skills

|题目|模式|外部判定|状态|
|---|---|---|---|
|ex01_popcount8|no_skills|L3|OK|
|ex02_detect_1101|no_skills|L1|FUNCTION_ERROR|
|ex03_lfsr8|no_skills|L1|FUNCTION_ERROR|

## 判定器与数据可信度

- 外部参考代码与测试台不传入模型消息；只暂存题面和接口文件。这是输入隔离，不是文件系统安全沙箱。
- 数据集含独立Python算出的预期值；6个正确实现均通过，6个恒0变异实现均失败。原同步复位检查缺口在本轮回归实验前已补上。
- 公开题首次部分综合没有正常完成，原始结果完整保留。随后统一缩短Windows EDA中间路径，对所有公开产物重新判定，未重新生成或修改RTL；rejudge.json保留原结果、阶段退出码与代码摘要核对。并未确认首轮异常的唯一根因。
- 内部L1/L2/L3是反馈里程碑，不等于外部判定，也不是赛事隐藏题集成绩。
- 修复后保守地拒绝无可靠参考的自测成功，可能使内部等级低于外部结果，这是预期行为。

## 失败分析

- public / ex02_detect_1101 / baseline：外部语义比较失败，不能被内部自测成功覆盖。
- public / ex03_lfsr8 / agent：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h01_alu / baseline：编译或展开错误；具体错误见xvlog/xelab日志。
- regression / h02_saturating_add / baseline：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h02_saturating_add / agent：编译或展开错误；具体错误见xvlog/xelab日志。
- regression / h03_priority_counter / baseline：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h03_priority_counter / agent：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h04_enabled_pipeline / baseline：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h04_enabled_pipeline / agent：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h05_pattern_0110 / baseline：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h05_pattern_0110 / agent：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h06_pattern_1001001 / baseline：外部语义比较失败，不能被内部自测成功覆盖。
- regression / h06_pattern_1001001 / agent：外部语义比较失败，不能被内部自测成功覆盖。
- no_skills / ex02_detect_1101 / no_skills：外部语义比较失败，不能被内部自测成功覆盖。
- no_skills / ex03_lfsr8 / no_skills：外部语义比较失败，不能被内部自测成功覆盖。

## 消融结论边界

只有“完整agent / 关闭skill / 单次官方baseline”的小样本比较，尚无关闭验证器、关闭Diff等独立消融。即使单题差异明显，也不能解释为统计显著提升。

## 后续尚未完成

- AMD ROCm正式环境、32GB峰值显存、断网Docker、冷启动和全题集墙钟验收。
- 更多独立题型与真正冻结的未见留出集，重复采样与更完整消融。
- signed/参数接口、辅助模块提取、模型声明与启动默认一致性、HTTP readiness与run.sh异常退出处理。
- 现有REPORT.md的14B/89分等宣称仍未核实；本轮7B实验不能作为证明。
