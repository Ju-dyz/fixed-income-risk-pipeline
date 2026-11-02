# 项目简介

## 数据流程

```text
合成市场数据 → CSV源文件 → Python统一采集 → MySQL Raw层 → SQL清洗与整合
→ Python风险计算 → MySQL结果层 → Excel报告
```

Python负责多来源数据采集、复杂金融计算和报告自动化。SQL负责数据库内的字段映射、标识符与单位标准化、重复/缺失/陈旧行情检查和大表Join。MySQL同时保存原始输入、标准化数据、历史风险结果和运行审计，并通过幂等upsert避免业务结果重复。

## 主要数据表

| 表 | 用途 |
|---|---|
| `instrument`, `portfolio` | 证券和组合主数据 |
| `raw_position`, `raw_market_data` | 原始输入留痕 |
| `position_daily`, `transaction_daily` | 标准化持仓和交易 |
| `market_data_daily` | 价格、收益率、利差和陈旧标记 |
| `risk_metric_daily` | 久期、凸性、DV01、CS01、关键利率 DV01 |
| `pnl_nav_daily` | NAV、P&L 和归因 |
| `portfolio_risk_daily` | 组合风险、VaR 和回测结果 |
| `stress_result_daily` | 压力测试结果 |
| `dq_exception`, `pipeline_run` | 数据质量和运行审计 |

## 计算约定

- 利率使用小数，利差冲击使用基点；价格按每 100 面值报价。
- DV01/CS01 表示利率或利差上升 1bp 的美元价值变化。
- 债券采用固定票息、平坦到期收益率模型；互换和 MBS 使用教学近似。
- VaR 使用最多 60 日历史模拟，至少需要 20 个观测值。
- 当前压力情景参考SVB倒闭期间的市场变化：2年利率下降50bp、10年利率下降10bp，形成牛市陡峭化；利率波动率由115升至180。
- 利率压力损益使用2Y和10Y Key Rate DV01计算。由于教学组合未建立完整期权定价模型，波动率损益仅对MBS敞口使用配置文件中明确标注的Vega proxy，不能视为生产级Vega。

## 常见问题

- 连接失败：检查 `.env`、MySQL 服务和用户权限。
- Python 包缺失：使用 `.venv/bin/python` 并安装 `requirements.txt`。
- 数据异常：查看 `data/processed/dq_exceptions.csv` 或 `dq_exception` 表。
- Excel 无法写入：关闭已打开的报告后重试。
- 重复运行：业务表使用主键和 upsert，不会重复插入。

