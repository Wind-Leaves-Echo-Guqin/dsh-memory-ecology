# experiments/ — 论文实验工件（治理消融 + 基线对比）

本目录是论文《治理优先的 Agent 记忆系统》的实验工件：消融 harness、基线对比脚本与原始结果数据。全部实验离线可复现（P2 的 Mem0 臂除外，其需要 LLM API key）。

## 复现

```sh
# RQ1 消融（零依赖，约 2 分钟）
python experiments/ablation.py --seeds 42,43,44,45,46 --sessions 30 --cands 10 --arms raw,g1,full

# RQ2 基线对比（需 DEEPSEEK_API_KEY 环境变量；约 15 分钟）
DEEPSEEK_API_KEY=<key> python experiments/baselines.py --arms full-context,rag-tfidf,governed,mem0

# 论文图表
python experiments/paper_figs.py
```

## 数据文件

- `results/summary.json` — RQ1 消融逐种子结果（5 种子 × 3 臂 × 30 会话）
- `results/baselines-summary.json` — RQ2 四臂问答对比（seed 42）

## 口径说明

- 消融三臂：A0 无治理 / A1 仅门① / A2 完整管线；工作负载 = 30 会话 × 10 候选，60 事实宇宙（6 领域 × 10 项目）。
- 指标定义与威胁效度分析见论文第 4 章；近重复对密度阈值 0.75（归一化 difflib）。
- RQ2 判定为确定性词面匹配（答案含当前正确 token）；Mem0 以官方推荐配置运行（DeepSeek LLM + HF 多语言嵌入 + FAISS 本地库）。
