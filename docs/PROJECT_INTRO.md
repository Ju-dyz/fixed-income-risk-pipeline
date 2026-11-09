# 项目简介

## 数据流程

```text
合成市场数据 → CSV源文件 → Python统一采集 → MySQL Raw层 → SQL检查与标准化
→ MySQL标准化表 → Python分析层Join与风险计算 → MySQL结果层 → Excel报告
```

Python负责多来源数据采集、分析所需的跨表Join、复杂金融计算和报告自动化。SQL负责从Raw JSON提取字段、执行标识符与单位标准化，并检查重复、缺失和陈旧行情；标准化后的Position、Market、Transaction、NAV和Actual P&L分别写入关系表，而不是预先合成一张分析宽表。MySQL同时保存原始输入、标准化数据、历史风险结果和运行审计，并通过幂等upsert避免业务结果重复。

## 程序结构与运行方式

- `DatabaseClient`管理MySQL连接、Raw导入、SQL标准化、运行审计和分析结果写回。
- `RiskPipeline`是端到端总控制器，适合原始数据变化后从上游重新运行完整流程。
- `PricingModel`保存定价假设，并调用债券、IRS、MBS和Key Rate风险函数。
- `HistoricalVaRModel`保存60日窗口、模拟结果并执行VaR backtesting。
- `python -m src.pipeline --full-history --load-db`执行合成数据生成、Raw导入、SQL标准化、Python分析、MySQL写回和Excel报告。当前教学版本会重新生成合成Raw文件；接入真实Bloomberg文件时应跳过`generate()`。
- `python -m src.run_analytics --date YYYY-MM-DD --persist --report`从现有MySQL标准化表开始，重新运行Pricing、P&L、VaR、Stress和Pricing Validation，并写回分析表和生成Excel；不会重新导入Raw数据，也不会执行`02_transform_raw.sql`。

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
- UST和公司债采用现金流贴现，明确计算Macaulay Duration、Modified Duration、标准凸性和DV01。IRS按固定腿减浮动腿估值，并以±1bp重估计算Effective Duration、Convexity和DV01；当前显式假设receive-fixed、半年固定付息和平坦曲线。MBS按现有合成现金流进行±1bp Effective Duration重估，但尚未包含生产级提前还款/OAS模型。
- Key Rate DV01使用相邻2Y/5Y/10Y/30Y节点的线性局部bump-and-reprice，不再使用反距离权重。
- VaR 使用完整60日历史模拟窗口；不足60组历史Security shock时，VaR保持为空。
- 当前压力情景参考SVB倒闭期间的市场变化：2年利率下降50bp、10年利率下降10bp，形成牛市陡峭化；利率波动率由115升至180。
- 利率压力损益使用2Y和10Y Key Rate DV01计算。由于教学组合未建立完整期权定价模型，波动率损益仅对MBS敞口使用配置文件中明确标注的Vega proxy，不能视为生产级Vega。
- Stress限额不与VaR比较，而按风险偏好设置为NAV比例；当前配置为NAV的2%。`stress_loss = max(-stress_pnl, 0)`，当Stress Loss大于`NAV × 2%`时设置breach flag。
- 日常P&L归因在证券层使用前一日风险敞口：`Duration P&L = -前日DV01 × 本证券收益率变化bp`，凸性项使用前日市值和凸性，`Spread P&L = -前日CS01 × 本证券利差变化bp`。
- Carry使用前一日证券数量和票息；仓位变化通过当日交易数量、交易价格与收盘价格计算Trading P&L。Hypothetical/Explained P&L为上述分项之和。
- NAV优先从标准化上游表读取；在教学/离线模式缺少NAV时，保留原始fallback：`NAV = Portfolio Market Value + 10,000,000`。Actual P&L仍必须来自上游P&L表，不使用`NAV.diff()`替代。
- `Residual P&L = Actual P&L - Hypothetical P&L`。
- Historical VaR位于独立的`src/var_model.py`。`calculate_historical_simulation_var()`固定使用T日前一日的Security风险敞口，将之前60天每一天的Security yield/spread shock应用到同一套敞口，产生60个模拟P&L并计算95%/99% VaR。`backtest_var()`再用T日Actual P&L与VaR比较，分别生成95%和99% breach dummy。历史不足完整60组shock时VaR为空。
- Pricing Validation自动核对DV01公式、KRD与DV01对账、Modified Duration公式、产品Duration convention和CS01规则，并输出汇总及失败明细；这些检查用于验证计算一致性，不替代Bloomberg或独立定价系统的生产级模型验证。

## 常见问题

- 连接失败：检查 `.env`、MySQL 服务和用户权限。
- Python 包缺失：使用 `.venv/bin/python` 并安装 `requirements.txt`。
- 数据异常：查看 `data/processed/dq_exceptions.csv` 或 `dq_exception` 表。
- Excel 无法写入：关闭已打开的报告后重试。
- 重复运行：业务表使用主键和 upsert，不会重复插入。

