# 见微 · 完整源代码

本目录包含当前四页应用的后端、前端、提示词、规则、名录、压缩示例库和测试。运行资源均包含在本目录中。无需登录。

## 直接运行

如果本目录中包含 `python/python.exe`，双击 **启动见微.cmd**，即可使用随包环境启动。在 Windows 运行包中无需安装依赖。

源码环境使用 Python 3.12+，在本目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/open_competition.py
```

也可双击本目录的 `启动见微.cmd`，自动创建环境并安装缺少的依赖。

浏览器地址：`http://127.0.0.1:8780/?mode=real&run=training-showcase`。保留启动窗口，按 Ctrl+C 停止。`--offline` 关闭新模型分析，`--port 8781` 更换端口，`--no-browser` 仅启动服务。

## 项目结构与主流程

| 路径 | 责任 |
| --- | --- |
| `scripts/open_competition.py` | 配置、初始库安装、重复启动检查、本地服务入口 |
| `scripts/run_product_replay.py` | HTTP 接口与静态网页，绑定本机地址，拒绝跨站写入 |
| `src/event_workbench/news_service.py` | 用户提交、队列、持久状态、截止时间、明确重试 |
| `news_intake.py` / `news_upload.py` | 输入格式、原文保存、导入与去重 |
| `news_processing.py` / `news_joint.py` | 一次识别和一次综合判断，局部接收、统一发布事务 |
| `news_budget.py` / `trial_transport.py` | 每次最多两次发送，预算与超时，网络子进程 |
| `deepseek_credentials.py` | 从配置或环境变量获取密钥，不写入请求日志 |
| `development_lexical_candidates.py` / `security_identity.py` | 公司、行业与证券身份候选 |
| `news_materials.py` / `product_downstream.py` | 相关材料召回、事项比较、排除展示专用数据 |
| `aggregation_v2.py` / `news_events.py` | 同一事项判断、事件与材料版本、历史保护 |
| `credibility_rules.py` / `review_scoring.py` | 按依据计算可信度 |
| `alert_rules.py` / `alert_review.py` | 影响维度、预警依据与发布规则 |
| `product_replay.py` / `product_research.py` | 四页查询、档案、关注、已读和研究待办 |
| `engineering/demos/zhiyan-demo/` | HTML、CSS、JavaScript 前端；无需构建 |
| `engineering/contracts/` | 结构化字段定义与模型提示词 |
| `data/` / `官方数据/` | 运行资源、证券名录、规则与事件定义；不含官方新闻 Excel 全集 |
| `tests/` | 无收费调用的功能检查，保存开发答复与合成答复分开 |
| `reproducibility/` | 历史评估的参考标签、冻结预测标签及逐篇运行数字；独立保存，不进入产品检索 |
| `scripts/reproduce_report_metrics.py` | 重新比较保存标签，复算技术报告比例与历史耗时；只用标准库，无模型调用 |

普通新新闻由 `news_service → news_processing → news_joint → news_events` 处理。首轮读取正文并识别主要事项，次轮结合相关材料判断关系、证据和影响；评分与入库在本地完成。每次主动分析最多发送两次模型请求。

七项业务输出为事件摘要、主体、对应监测对象及事件分类、正负面方向、可信度与依据、事项进展、影响与预警依据；实际字段及映射参见输出约定和第 5 份算法文档。

## 模型与配置

`config/deepseek.json` 已预配演示凭据。`DEEPSEEK_API_KEY` 环境变量优先于配置文件。两轮均通过官方 API 调用 `deepseek-flash`，无需下载模型权重。新分析需要网络和供应商可用额度。

配置含 `api_key`、`port`、`send_model`、`model_budget_rmb`。默认端口 8780、本地账本上限 3 元，另受现有分阶段预算约束；并非账号余额或供应商全局限额。更换密钥可运行 `scripts/configure_competition_key.py`，保存过程不联网。修改配置后重启。

## 数据与持久化

初始库为 `data/示例事件库.sqlite3.gz`，含 160 条保存事件、238 份所需材料及 242 个历史保存版本。首次启动校验并安装到 `artifacts/eng063/local/workbench.sqlite3`，后续不会覆盖工作库。

默认关注页预置三个案例。新输入、关注、已读、待办和预警操作保存在工作库；停止程序后备份整个 `artifacts/eng063/local`。压缩初始库保持不变。

## 测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -I -m unittest discover -s tests -v
```

自带环境使用 `python\python.exe -X utf8 -I -m unittest discover -s tests -v`。测试使用临时目录、保存开发答复或本地合成服务，不调用 DeepSeek 收费接口，不代表识别准确率。不要把测试参考答案加入检索输入。

完整部署、使用、配置和常见问题见仓库根 README；Windows 运行包内同时附 `完整部署说明.md`。

历史报告数字复算：`python -X utf8 scripts/reproduce_report_metrics.py`。逐篇标签和统计口径见 [复算说明](reproducibility/README.md)。对话组比例与程序运行耗时分别统计，不将对话成绩当作自动系统准确率。
