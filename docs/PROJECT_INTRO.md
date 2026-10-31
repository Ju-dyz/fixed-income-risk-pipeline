# 项目简介

## 数据流程

```text
合成市场数据 → CSV 源文件 → Python 清洗和风险计算 → MySQL → Excel 报告
```

Python 负责字段映射、标识符和单位标准化、数据质量检查及风险计算。MySQL 负责持久化、主外键约束、运行审计和幂等 upsert。

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
- 压力损益近似为 `-DV01 × 利率冲击bp - CS01 × 利差冲击bp`。

## 常见问题

- 连接失败：检查 `.env`、MySQL 服务和用户权限。
- Python 包缺失：使用 `.venv/bin/python` 并安装 `requirements.txt`。
- 数据异常：查看 `data/processed/dq_exceptions.csv` 或 `dq_exception` 表。
- Excel 无法写入：关闭已打开的报告后重试。
- 重复运行：业务表使用主键和 upsert，不会重复插入。

