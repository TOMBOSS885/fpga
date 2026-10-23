"""Summarize actual saved runs without awarding an official competition score."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]

def load(relative):
    path=ROOT/relative
    return json.loads(path.read_text(encoding='utf-8'))

def main():
    public=load('experiments/public_compare_20261006/results.json')
    replay=load('experiments/public_compare_20261006/rejudge.json')
    regression=load('experiments/regression_compare_20261006/results.json')
    ablation=load('experiments/public_no_skills_20261006/results.json')
    health=load('experiments/fixture_health_20261006/results.json')
    summary={'development_only':True,'warning':'Small known/public sets, one sample per mode. NOT official grades, not unseen pass@k, not AMD deployment verification.', 'suites':{}}
    text=['# 本地真实模型评测与失败分析','',
          '模型：本机 Qwen2.5-Coder 7B Instruct Q4_K_M（Ollama）。显卡：用户RTX4060 Laptop 8GB；不是比赛AMD硬件。',
          '同一模型服务、同一上下文服务配置；官方baseline原样使用。每模式每题只有一次采样，不能据此推广总体能力或保证获奖。',
          '公开3题用于冒烟；6题为已知开发回归集，不是未见留出集。关闭skill仅改变技能加载，不关闭多角色与反馈。','']
    for name,rows in [('public',replay['results']),('regression',regression['results']),('public_no_skills',ablation['results'])]:
        text+=['## '+name,'','|题目|模式|外部判定|状态|','|---|---|---|---|']
        summary['suites'][name]={}
        for row in rows:
            text.append('|{}|{}|{}|{}|'.format(row['task'],row['mode'],row['level'],row['status']))
        for mode in sorted({r['mode'] for r in rows}):
            selected=[r for r in rows if r['mode']==mode]
            graded=[r for r in selected if r.get('coefficient') is not None]
            summary['suites'][name][mode]=dict(total=len(selected),graded=len(graded),
                excluded=[r['task'] for r in selected if r.get('coefficient') is None],
                external_function_pass=sum(r['level'] in ('L2','L3') for r in graded),
                external_l3=sum(r['level']=='L3' for r in graded),
                local_mean_coefficient=sum(r['coefficient'] for r in graded)/len(graded) if graded else None)
        text.append('')
    text+=['## 判定器与数据可信度','',
           '- 外部参考代码与测试台不传入模型消息；只暂存题面和接口文件。这是输入隔离，不是文件系统安全沙箱。',
           '- 数据集含独立Python算出的预期值；6个正确实现均通过，6个恒0变异实现均失败。原同步复位检查缺口在本轮回归实验前已补上。',
           '- 公开题首次部分综合没有正常完成，原始结果完整保留。随后统一缩短Windows EDA中间路径，对所有公开产物重新判定，未重新生成或修改RTL；rejudge.json保留原结果、阶段退出码与代码摘要核对。并未确认首轮异常的唯一根因。',
           '- 内部L1/L2/L3是反馈里程碑，不等于外部判定，也不是赛事隐藏题集成绩。',
           '- 修复后保守地拒绝无可靠参考的自测成功，可能使内部等级低于外部结果，这是预期行为。','',
           '## 失败分析','']
    for row in replay['results']+regression['results']+ablation['results']:
        if row['status'] in ('OK','OK_SIM'):continue
        suite='public' if row in replay['results'] else 'regression' if row in regression['results'] else 'no_skills'
        explanation={'CODE_ERROR':'编译或展开错误；具体错误见xvlog/xelab日志。',
                     'FUNCTION_ERROR':'外部语义比较失败，不能被内部自测成功覆盖。',
                     'SYNTH_ERROR':'综合未满足判定，需看退出码和原生日志。',
                     'GENERATION_TIMEOUT':'生成超过本轮开发预算。',
                     'TOOL_CRASH':'原生工具异常退出，单列而不是声称电路失败。'}.get(row['status'],'未产生可靠判定，保留原始证据继续排查。')
        text.append('- {} / {} / {}：{}'.format(suite,row['task'],row['mode'],explanation))
    text+=['','## 消融结论边界','',
           '只有“完整agent / 关闭skill / 单次官方baseline”的小样本比较，尚无关闭验证器、关闭Diff等独立消融。即使单题差异明显，也不能解释为统计显著提升。','',
           '## 后续尚未完成','',
           '- AMD ROCm正式环境、32GB峰值显存、断网Docker、冷启动和全题集墙钟验收。',
           '- 更多独立题型与真正冻结的未见留出集，重复采样与更完整消融。',
           '- signed/参数接口、辅助模块提取、模型声明与启动默认一致性、HTTP readiness与run.sh异常退出处理。',
           '- 现有REPORT.md的14B/89分等宣称仍未核实；本轮7B实验不能作为证明。','']
    summary['fixture_health']=dict(positive_pass=sum(r['mode']=='positive' and r['level']=='L2' for r in health['results']),
        stuck_zero_rejected=sum(r['mode']=='stuck_zero' and r['status']=='FUNCTION_ERROR' for r in health['results']))
    summary['source_changed_during_generation']={k:v.get('source_changed_during_run') for k,v in [('public',public),('regression',regression),('no_skills',ablation)]}
    folder=ROOT/'experiments/evaluation_summary_20261006'
    folder.mkdir(exist_ok=True)
    (folder/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (ROOT/'docs/evaluation_report_20261006.md').write_text('\n'.join(text),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
