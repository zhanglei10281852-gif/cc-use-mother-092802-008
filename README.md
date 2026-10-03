# 气候承诺进度汇编服务

编制国家自主贡献（NDC）进展材料时，能源、交通、工业等部门先后提交指标，
口径各异（实物量 / 强度、单位、基准年、统计边界随政策调整变化）。
本服务把**目标、分部门指标、基准年、测算方法与来源材料**建成有版本的证据图，
保证：

- 编辑人员**不能为凑总数而把口径不同的值直接相加**；
- 自动检测**指标覆盖重叠、单位/边界不兼容、缺少来源的估算值**；
- 显式区分 **“未报告”（not reported）与“报告为零”（reported zero）**；
- 国家汇总**只能由兼容分项推导**（实物量求和；强度/占比按驱动量加权）；
- 支持**分阶段锁定章节、记录审阅意见、退回后重新提交**；
- 可导出**指定版本的报告数据包与版本差异摘要**；
- **晚到的部门修订不会改变已对外确认的旧版本**，且每个汇总值可经
  报送记录追溯到具体来源材料。

## 架构

| 模块 | 职责 |
| --- | --- |
| `src/commitment_report/contracts.py` | 不可变数据契约：指标、边界、材料、目标、报送、审阅、问题 |
| `src/commitment_report/validation.py` | 口径校验引擎（重叠 / 单位 / 基准年 / 边界 / 来源 / 驱动量） |
| `src/commitment_report/aggregation.py` | 仅由兼容分项按**期次**推导国家汇总 |
| `src/commitment_report/store.py` | 版本化证据图、章节锁定、评审流转、快照与差异 |
| `src/commitment_report/api.py` | 标准库 HTTP API（无第三方依赖） |
| `examples/demo.py` | 三部门报送—退回—重报—确认—晚到修订的完整示例 |

关键设计：所有报送都是**不可变记录**；部门修订总是生成挂接 `supersedes`
链的新记录；`confirm_version` 冻结快照，之后的任何修订都只影响工作态与新版本。

## 运行

```bash
# 测试
python -m unittest discover -s tests -v
# 编译检查
python -m compileall -q src tests
# 端到端示例
PYTHONPATH=src python3 examples/demo.py
# HTTP 服务
PYTHONPATH=src python -m commitment_report.api --host 127.0.0.1 --port 8080
```

## HTTP API 摘要

| 方法与路径 | 说明 |
| --- | --- |
| `POST /evidence` `/boundaries` `/indicators` `/targets` | 登记证据图要素 |
| `POST /submissions` | 部门报送；`value: null`=未报告，`0`=报告为零；`supersedes`=重新提交 |
| `POST /submissions/{id}/review` | 记录审阅意见：`approve` / `return` |
| `POST /chapters/{code}/lock` `/unlock` | 分阶段锁定/解锁章节 |
| `GET /chapters/{code}/events` | 章节操作记录 |
| `GET /issues` | 全部口径问题（error / warning / info） |
| `GET /aggregates` | 由兼容分项推导出的国家汇总（按期次） |
| `POST /versions/confirm` | 冻结并对外确认当前版本 |
| `GET /versions/{n}` | **导出指定版本完整报告数据包** |
| `GET /versions/{a}/diff/{b}` | **版本差异摘要** |
| `GET /current` | 当前草稿数据包 |

## 校验规则

| 代码 | 级别 | 含义 |
| --- | --- | --- |
| `overlap` | error | 同期次两个分项覆盖同一原子部门，相加将重复计算 |
| `unit_mismatch` | error | 报送单位与指标口径单位不一致 |
| `baseline_mismatch` | error | 基准年不一致 |
| `boundary_mismatch` | error | 统计边界不兼容（不同等价组的边界修订） |
| `boundary_revision_conflict` / `unknown_boundary` | error | 边界声明问题 |
| `estimated_without_source` / `missing_source` | error | 估算值无来源 / 报送无来源 |
| `unknown_evidence` / `unknown_indicator` | error | 引用未登记对象 |
| `missing_driver` / `driver_unit_mismatch` | error | 强度/占比指标缺加权驱动量 |
| `mixed_methodology` | warning | 分项混用测算方法 |
| `not_reported` | info | 应有部门该期次未报送（**不等于零**） |

任一 error 都会阻断该指标该期次的汇总（输出 `method: "blocked"` 而不是数值）。

## 可追溯链

导出包的每个汇总行：

```
aggregate.contributing_submission_ids[]
  └─ submissions[].evidence_ids[]
       └─ evidence[]  （标题、出处、发布日期、URI、sha256）
```

版本差异中的 `head_component_ids` 精确标出汇总变化由哪些新/旧报送造成，
因此“修改后的汇总”总能回到具体材料。
