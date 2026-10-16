# 通知更新演示（6 步）

执行前确认演示源图是已加载的正式数据图。以下使用本仓库当前源图 `oncall_brain`，目标为 `oncall_brain_freshness_demo`。不修改 `.env`。端口另选空闲端口，避免与正式服务的 8000 冲突。

1. **复制并启动演示图服务**。PowerShell 中执行：
   ```powershell
   python -c "from oncall_brain.freshness import clone_demo_graph; print(clone_demo_graph('oncall_brain','oncall_brain_freshness_demo'))"
   $env:GRAPH_NAME='oncall_brain_freshness_demo'
   $env:DEMO_GRAPH=$env:GRAPH_NAME
   $env:ONCALL_NOW='2026-10-16'
   $env:WEB_PORT='18002' # 操作者先确认空闲
   python -m web.app
   ```
   `GRAPH_NAME` 控制现有问答读取图；`DEMO_GRAPH` 控制通知写入和删除目标，两者必须一致。复制到已存在的图会失败，不覆盖原图。
2. **问**。打开该服务的 `/demo`，点击“默认步骤超时多少分钟？”。保留默认规则 ID `Rule:Default_Step_Timeout_60_Minutes`；当前为 60 分钟，`superseded=[]`。此页直接读取现有 `/api/node`，不调用大模型；原首页 `/api/ask` 接口保持不变。
3. **加通知**。保留页面预填的 30 分钟正文和 `2026-10-16`，点击“添加新通知”。调用 `POST /api/demo/notice`，body 为 `name,text,supersedes_id,effective`，响应包含通知 ID、证据 ID、权威级别及复制关系数。日期采用 YYYY-MM-DD；未来通知只有到生效日期才可见。
4. **再问同一个问题**。再次点击同一按钮，页面跟随 SUPERSEDES 读取新通知，显示 30 分钟正文和来源证据。不会自动修改输入框中的旧规则 ID。
5. **看旧规则变灰**。旧卡片以灰色和删除线标记“已替代”，`superseded` 显示新通知 ID、生效日期；新通知正常显示。本次真实图验证：`[]` → `[{superseded_by: Notice:Demo_Timeout_30, aid: demo:Notice:Demo_Timeout_30:supersedes, effective: 2026-10-16}]`；新通知继承 1 条 `APPLIES_TO`，从 `Stage:Pipeline_Step` 可读取。60 分钟节点本身没有适用边，故沿其替代链继承 120 分钟节点的可见范围。新边重新使用新证据，不携带旧数值声明。
6. **还原**。点击“还原：删除演示图”（`POST /api/demo/reset`），或执行 `python -c "from oncall_brain.freshness import reset_demo; print(reset_demo('oncall_brain_freshness_demo'))"`。这会删除整张演示图，原图不变；再次演示须重新复制。操作者自行结束演示服务并清除本终端演示环境变量。开发验证已执行删除并确认演示图不存在。

安全边界：未配置 `DEMO_GRAPH` 或目标不满足 `_demo` 命名时，两条写路由均返回 403。数据库异常使用已有 503 脱敏处理。通知节点、证据及所有边一次查询写入。自由文本不推断结构化数值，`claims_json=[]`、`claim_subjects=[]`；正文及证据保留完整新规则，结构化语义槽提取不在本任务范围。未实测大模型生成答案或浏览器布局；已实测图读取更新和还原。
