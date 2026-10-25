# 宿主生命周期记录

这里只定义文脉校验器接收的规范化控制记录，不是任何宿主的原生 API 字段。主编须从真实宿主能力约定、终态消息、实际库存调用和本批派工账簿转录身份，并保存原始返回文件。下面JSON是形态示例，必须用实际记录填入；示例和合成测试都不是岗位回收凭据。

所有下列规范化对象的字段集合都须准确匹配；未知字段、错误类型和数组形状拒绝。所有控制文件及其原始证据文件必须位于 registry.control_root 内。文件引用准确为 `{"path":"相对工作区路径","sha256":"实际文件字节的SHA256"}`，不接收 provenance 字符串。路径出界、链接/重解析点、与输入或可写交付的硬链接、版本改变及核验期间字节改变仍拒绝。原始宿主返回格式不由本脚本猜测或重写；脚本核对被引用文件的 SHA 与保存字节，不能鉴真其来源或自动证明转录语义真实。

## 模式及逐任务选择

省略 host_lifecycle 或设为 `{"mode":"close_agent"}`，继续旧 close_agent 契约；此模式出现 slot_released 或 release_receipt 即拒绝，包括 slot_released=false。

新模式必须显式声明，并引用本批能力记录：

```json
{
  "mode": "terminal_inventory",
  "close_agent_available": false,
  "terminal_releases_slot": true,
  "capability_receipt": {"path": "control/capabilities.json", "sha256": "实际SHA256"}
}
```

capability_receipt 文件准确为：

```json
{
  "schema": "wenmai.host-lifecycle.v1",
  "run_id": "RUN",
  "mode": "terminal_inventory",
  "close_agent_available": false,
  "terminal_releases_slot": true,
  "evidence": {"path": "control/raw-capabilities.txt", "sha256": "实际SHA256"}
}
```

能力布尔必须为 JSON false/true，不把 0/1 当布尔。本模式每项任务自行选择历史 closed=true + close_receipt，或 closed=false + slot_released=true + release_receipt；同一任务不能混用。历史关闭记录继续只由旧 close_agent 凭据证明，不因当前宿主缺工具而改写。未回收任务可省略 slot_released 或设 false，但不能附 release_receipt；close_receipt 也只能附在 closed=true 的任务。

queued/running 不能回收。queued 不能带 agent、delivery、close/release 凭据。只有完成消息、只设置布尔、缺准确交付或缺终态/库存证据，均不能回收。没有请求回收的 returned/failed/cancelled 代理继续占槽；superseded 必须已有核验关闭或回收。

## 释放、终态及库存文件

RUN/TASK/ATTEMPT/AGENT 是同一任务的四重身份占位符，不是可重用的示例凭据。release_receipt 文件准确为：

```json
{
  "schema": "wenmai.slot-release.v1",
  "run_id": "RUN",
  "task_id": "TASK",
  "attempt_id": "ATTEMPT",
  "agent_id": "AGENT",
  "operation": "terminal_inventory",
  "observation_id": "OBSERVATION",
  "terminal_event": {"path": "control/terminal.json", "sha256": "实际SHA256"},
  "inventory": {"path": "control/inventory.json", "sha256": "实际SHA256"}
}
```

terminal_event 文件准确为：

```json
{
  "schema": "wenmai.terminal-event.v1",
  "run_id": "RUN",
  "task_id": "TASK",
  "attempt_id": "ATTEMPT",
  "agent_id": "AGENT",
  "observation_id": "OBSERVATION",
  "status": "completed",
  "evidence": {"path": "control/raw-terminal.txt", "sha256": "实际SHA256"}
}
```

inventory 文件准确为：

```json
{
  "schema": "wenmai.agent-inventory.v1",
  "run_id": "RUN",
  "observation_id": "OBSERVATION",
  "complete": true,
  "agents": [
    {"run_id": "RUN", "task_id": "TASK", "attempt_id": "ATTEMPT", "agent_id": "AGENT", "status": "completed"}
  ],
  "evidence": {"path": "control/raw-inventory.txt", "sha256": "实际SHA256"}
}
```

observation_id 是主编关联这组控制快照的非空标识；三文件必须一致。相同文字本身不证明来自同一次实际调用，也不证明时间次序、实时性或真实观察。原始终态及库存可以是不同的实际宿主返回，必须保留各自准确字节。

库存是主编依据原始完整库存和本批派工身份表转录的**本批完整库存**，不是每任务只挑一个有利代理的列表。complete 必须准确为 true；agents 必须是数组，每项须准确含四身份和 status。四身份非空字符串、run_id 同批、agent_id 不重复；全部项的 status 只接收 running/completed/failed/cancelled。库存中每项四重身份都须在本批registry有对应登记；额外未登记岗位或其他岗位身份矛盾时，先对账，不能忽略它而报空槽。目标代理必须正向出现一次，四身份与终态事件一致，status 也一致。目标消失、仅凭未列出而推断回收、unknown、错误身份、跨批重放均拒绝。本版刻意不推断“已从宿主库存移除”的语义；这类宿主需另定义并测试契约。

规范化终态只接收 completed/failed/cancelled；原始宿主无法明确映射时保持未知，不造映射。任务 returned 只可对应 completed；failed 只可对应 failed；cancelled 只可对应 cancelled。superseded 是本地尝试状态，可对应三个已证终态之一。failed/cancelled/superseded 可无 delivery 而回收；若在新模式附 delivery，仍须核准完整输入/候选/报告/读程及身份，但不记成功交付。returned 总须先准确收件。编辑替补只在旧失败/取消/被替代尝试已有真实核验关闭或回收后放行；同一章仍只一项当前编辑。

## 输出边界

脚本分别返回 tool_closures_verified 与 released_slots_verified，open_agents 按未核实关闭或回收的任务计算。输出仍为 integrity_only、declarations_only=true、live_inventory_verified=false、literary_certification=false。这些结果只证明所给文件与声明一致，不能代替真实宿主调用、正文阅读或文学取舍。见[批量审修](batch-revision.md)。
