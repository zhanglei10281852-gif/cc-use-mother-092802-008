# 气候承诺进展汇编

编制国家自主贡献（NDC）进展材料的汇编服务。把目标、分部门指标、基准年、
测算方法和来源材料建成**有版本的证据图**，在汇总前检查单位、基准年和
统计边界是否可比，口径不同的值不得直接相加。

## 核心规则

- **三态报送值**：`reported`（含 `0`，即“报告为零”）与 `not_reported`
  （未报告）严格区分；汇总时未报告的分项不按零处理，结果记为
  `partial` 并列出缺口。
- **兼容才可汇总**：分项的单位、量纲（实物量/强度）、统计边界版本、
  基准年完全一致且覆盖子行业不重叠，才允许推导国家汇总；否则结果为
  `blocked`，不出数。
- **估算必须有来源**：`is_estimate=true` 的指标未引用来源材料时产生
  `ESTIMATE_WITHOUT_SOURCE` 错误并阻止汇总。
- **不可变版本**：所有节点按 `(kind, id, version)` 存储，修订只追加
  新版本；报告一经 `confirm` 即冻结快照，晚到的部门修订不会改变已
  对外确认的旧版本。
- **可追溯**：汇总结果携带精确到版本的来源材料与测算方法清单
  （`provenance`），导出数据包内含来源清单与内容哈希。

## 章节工作流

```
DRAFT → SUBMITTED → UNDER_REVIEW → LOCKED
                      ↓（退回）
                   RETURNED →（重新提交，轮次+1）→ SUBMITTED
```

支持按阶段批量锁定（`POST /stages/{stage}/lock`），审阅意见按提交轮次
记录并保留；锁定的章节拒绝任何修改。

## 运行

```bash
# 启动服务（纯标准库，无第三方依赖）
PYTHONPATH=src python3 -m commitment_report --port 8080

# 测试
python3 -m unittest discover -s tests -v

# 编译检查
python3 -m compileall -q src tests
```

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/sources` | 登记来源材料（自动递增版本） |
| POST | `/methods` | 登记测算方法 |
| POST | `/indicators` | 登记指标首个版本（`value: null` 表示未报告） |
| POST | `/indicators/{id}/revisions` | 部门修订，追加新版本 |
| GET | `/indicators/{id}?version=n` | 读取指标（默认最新版） |
| POST | `/targets` | 登记国家自主贡献目标 |
| POST | `/chapters` / `/chapters/{id}/submit` … | 章节创建、提交、审阅、退回、锁定 |
| POST | `/chapters/{id}/comments` | 记录审阅意见 |
| POST | `/stages/{stage}/lock` | 分阶段锁定章节 |
| GET | `/validation` | 当前口径校验结果 |
| POST | `/aggregation/national` | 国家汇总预览（不落库） |
| POST | `/reports` / `/reports/{id}/confirm` | 创建报告并确认冻结版本 |
| GET | `/reports/{id}/versions/{n}/export` | 导出指定版本数据包 |
| GET | `/reports/{id}/diff?from=1&to=2` | 两版本差异摘要 |

证据引用支持 `"SRC-1"`（固定到当前最新版）与 `"SRC-1@2"`（固定到指定
版本）两种写法，写入后一律按具体版本存储。

## 目录结构

```
src/commitment_report/
  values.py       三态报送值（未报告 ≠ 零）
  model.py        证据图节点：目标/指标/来源/方法（不可变版本）
  graph.py        版本化证据图与溯源边
  validation.py   重叠、口径兼容性、无来源估算检测
  aggregation.py  国家汇总推导（仅兼容分项，携带溯源）
  workflow.py     章节分阶段锁定与审阅流转
  reports.py      确认快照、数据包导出、版本差异
  service.py      应用服务门面
  api.py          标准库 HTTP API
tests/            unittest 测试（含端到端 API 流程）
```
