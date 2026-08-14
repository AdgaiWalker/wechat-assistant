# Responses API Message ID 兼容性问题调查与修复方案

## 1. 文档目的

本文记录一次 Codex 调用第三方 Responses API 网关时出现的会话中断问题，给出完整证据链、根本原因、修复方案和验收标准。

问题的直接表现是：对话在工具调用或继续生成时突然失败，并返回如下错误：

```text
Invalid 'input[28].id': 'item_437a24c15c2231f767fe7306'.
Expected an ID that begins with 'msg'.
```

调查确认，该问题不是用户输入、Codex 提示词或业务项目造成的，而是 Responses API 适配层生成了类型与 ID 前缀不匹配的消息对象。

## 2. 结论摘要

网关通过 `/v1/responses` 返回了一个 `type: "message"`、但 ID 以 `item_` 开头的对象：

```json
{
  "type": "message",
  "id": "item_437a24c15c2231f767fe7306",
  "role": "assistant",
  "content": [
    {
      "type": "output_text",
      "text": "..."
    }
  ]
}
```

Codex保存该响应后，会在下一次模型采样时将它作为历史输入重新提交。网关随后对输入进行校验，发现 `message` 的 ID 不是 `msg_...`，因此返回 HTTP 400。

完整故障链路如下：

```text
网关返回非法 message ID
        ↓
Codex保存响应对象
        ↓
工具调用结束或用户继续对话
        ↓
Codex将历史对象放入下一次 input
        ↓
网关校验 input[n].id
        ↓
HTTP 400：Expected an ID that begins with 'msg'
```

这不是纯粹的随机故障。只有当网关某一次响应生成了非法的 `item_...` 消息 ID，并且该消息随后被重新提交时，错误才会出现。

## 3. 运行环境

本次案例中的相关环境如下：

- 客户端：Codex Desktop
- Codex CLI：`0.145.0-alpha.18`
- 模型：`gpt-5.6-sol`
- API 协议：Responses API
- 请求路径：`POST /v1/responses`
- 模型提供方配置：`quick_setup_gateway_2`
- 网关实现标识：`x-new-api-version: d43d7df6083b857fa169130fcad5e5a865f9e191`

## 4. 实际故障记录

### 4.1 首次请求成功

网关首先返回 HTTP 200：

```text
HTTP 200 OK
x-oneapi-request-id: REDACTED_REQUEST_ID
content-type: text/event-stream
```

该流式响应中出现了如下输出项：

```json
{
  "type": "message",
  "id": "item_437a24c15c2231f767fe7306",
  "role": "assistant",
  "content": [
    {
      "type": "output_text",
      "text": "<thinking>**Providing update during search**</thinking>\n先说明当前假设排序：最可能是刷新脚本在微信替换/写入 DB 的窗口只校验一次；其次是失败后仍记录新 mtime，导致下一轮跳过；再次才是密钥真的变化。我会用现有测试结构验证前两项。"
    }
  ]
}
```

本地日志确认 Codex 从响应流中接收到的对象类型和 ID 为：

```text
Output item item_type="message" item_id="item_437a24c15c2231f767fe7306"
```

因此，异常 ID 在成功响应阶段就已经出现，并不是用户在请求体中手工构造的。

### 4.2 后续采样失败

同一轮执行中，模型同时发起了工具调用。工具执行完成后，Codex需要携带工具结果继续调用模型，于是将前述消息对象重新放入 `input`。

网关返回：

```text
HTTP 400 Bad Request
x-oneapi-request-id: REDACTED_REQUEST_ID
```

错误响应为：

```json
{
  "error": {
    "message": "Invalid 'input[28].id': 'item_437a24c15c2231f767fe7306'. Expected an ID that begins with 'msg'.",
    "type": "invalid_request_error",
    "param": "input[28].id",
    "code": "invalid_value"
  }
}
```

`input[28]` 表示请求数组中索引为 28 的输入项，即第 29 项。这里的计数包含系统指令、开发者消息、工具定义、推理项、工具调用结果等内部上下文，并不等于用户看到的第 29 条聊天消息。

## 5. 根本原因

### 5.1 对象类型和 ID 前缀不一致

Responses API 的输出由多种 item 组成。虽然 message 本身也是一个 output item，但它仍然具有明确的消息对象类型和 ID 约束。

本次异常对象同时满足：

```text
type = message
id   = item_...
```

网关的请求校验器又要求：

```text
type = message
id   = msg_...
```

因此网关产生了一个自己后续无法接受的对象。

### 5.2 可能的实现缺陷

Responses API 转换层可能对所有输出项统一调用了类似逻辑：

```go
item.ID = GenerateID("item")
```

但对于消息对象，应根据具体类型生成 ID：

```go
item.ID = GenerateID("msg")
```

另一种可能是上游模型已经返回了 `item_...`，但网关在转换为 `type: "message"` 时保留了原始 ID，没有同步规范化 ID 前缀。

无论 ID 最初由网关还是上游生成，Responses API 适配层都应在向客户端发送之前保证最终对象符合对外协议。

### 5.3 为什么表现为偶发

本机历史记录中既存在正确的 `msg_...` 消息，也存在错误的 `item_...` 消息。这说明不同响应路径使用了不同的 ID 生成或转换逻辑。

容易触发问题的场景包括：

- assistant commentary 与工具调用同时出现在一次响应中；
- 工具执行后需要在同一轮继续采样；
- 长对话中重放历史 output items；
- Chat Completions 响应转换为 Responses API 事件；
- 流式 SSE 和非流式响应使用了不同的对象构造器。

只要异常消息没有被再次提交，当前响应仍可能正常显示；一旦进入下一次采样，非法 ID 才会触发 HTTP 400。因此用户感知为“使用一段时间后随机中断”。

## 6. 修复目标

修复需要同时满足两个目标：

1. 不再产生新的非法 message ID。
2. 已经包含非法 ID 的历史对话仍能继续使用。

如果只修复出站响应，新对话可以恢复正常，但之前已经保存错误对象的 Codex 任务仍可能持续失败。

## 7. 修复方案

### 7.1 修复出站响应的 ID 生成

所有对外返回的消息对象都必须满足：

```json
{
  "type": "message",
  "id": "msg_..."
}
```

建议集中实现一个按对象类型生成 ID 的方法，避免每条转换路径自行决定前缀：

```go
func GenerateOutputItemID(itemType string) string {
    switch itemType {
    case "message":
        return GenerateID("msg")
    case "reasoning":
        return GenerateID("rs")
    case "function_call":
        return GenerateID("fc")
    default:
        return GenerateID("item")
    }
}
```

实际前缀应以项目已经采用的 Responses API 规范为准，但 `message → msg_` 必须保持一致。

需要检查以下响应构造路径：

- 非流式 Responses API 输出；
- SSE 流式输出；
- `response.output_item.added`；
- `response.output_item.done`；
- `response.completed` 中的最终 output；
- assistant commentary；
- 工具调用前后的 assistant message；
- Chat Completions 到 Responses API 的转换器；
- 上游响应透传和重新封装逻辑。

同一个对象出现在多个 SSE 事件中时，必须始终使用同一个 ID。

### 7.2 增加出站协议校验

在发送 SSE 事件或 JSON 响应之前，增加对象一致性检查：

```go
func ValidateOutputItem(item OutputItem) error {
    switch item.Type {
    case "message":
        if item.ID == "" || !strings.HasPrefix(item.ID, "msg_") {
            return fmt.Errorf(
                "message output ID must begin with msg_: %q",
                item.ID,
            )
        }
    }

    return nil
}
```

生产环境不应把该内部错误直接暴露给客户端。发现非法 ID 时，可以重新生成合法 ID，同时记录带请求 ID 的告警日志。

### 7.3 兼容历史非法 ID

请求进入 `/v1/responses` 后，对历史输入进行规范化。

当输入满足以下条件时：

```json
{
  "type": "message",
  "id": "item_..."
}
```

建议删除非法 ID，让后续逻辑重新生成或把它作为无 ID 的输入消息处理：

```go
func NormalizeInputItems(items []InputItem) {
    for i := range items {
        item := &items[i]

        if item.Type == "message" &&
            item.ID != "" &&
            !strings.HasPrefix(item.ID, "msg_") {
            item.ID = ""
        }
    }
}
```

不建议简单地把 `item_` 字符串替换为 `msg_`。旧 ID 可能来自其他对象命名空间，直接替换可能造成对象关联冲突。删除后重新生成通常更安全。

如果项目依赖 ID 查找服务端存储对象，则应明确区分两种情况：

- ID 只作为客户端历史标识：可以删除或重新生成；
- ID 用于服务端对象引用：需要建立旧 ID 到新 ID 的映射，或将该消息转为完整内联输入。

### 7.4 统一流式与非流式转换逻辑

流式和非流式接口不应分别实现一套 message 构造逻辑。建议先生成统一的内部对象，再由不同编码器输出 JSON 或 SSE：

```text
上游响应
   ↓
统一 ResponseItem 模型
   ↓
类型校验和 ID 规范化
   ├── JSON encoder
   └── SSE encoder
```

这样可以避免只有流式输出使用 `item_...`、非流式输出使用 `msg_...` 的行为分叉。

## 8. 测试方案

### 8.1 Message ID 前缀测试

对所有转换结果进行遍历，确保 message 使用 `msg_`：

```go
func TestResponsesMessageUsesMsgPrefix(t *testing.T) {
    response := ConvertUpstreamResponse(upstreamFixture)

    for _, item := range response.Output {
        if item.Type == "message" {
            require.True(t, strings.HasPrefix(item.ID, "msg_"))
        }
    }
}
```

### 8.2 SSE 事件 ID 一致性测试

同一条 message 可能出现在以下事件中：

```text
response.output_item.added
response.content_part.added
response.output_text.delta
response.output_item.done
response.completed
```

测试应断言：

- ID 以 `msg_` 开头；
- 所有事件中的 ID 完全一致；
- output index 和 content index 保持一致；
- 不会在事件处理中重新生成 ID。

### 8.3 工具调用后的继续采样测试

该测试最接近真实故障场景：

1. 第一次请求让模型输出 assistant message 和 function call。
2. 保存第一次响应中的 output items。
3. 添加 function call output。
4. 把这些对象作为第二次请求的 `input`。
5. 第二次请求应成功返回最终答案。

测试输入结构示例：

```json
[
  {
    "type": "message",
    "id": "msg_test_assistant_message",
    "role": "assistant",
    "content": [
      {
        "type": "output_text",
        "text": "我先检查相关文件。"
      }
    ]
  },
  {
    "type": "function_call",
    "id": "fc_test_call",
    "call_id": "call_test",
    "name": "exec",
    "arguments": "{}"
  },
  {
    "type": "function_call_output",
    "call_id": "call_test",
    "output": "检查完成"
  }
]
```

预期：第二次调用不得返回任何 message ID 前缀错误。

### 8.4 历史非法 ID 兼容测试

输入一条历史污染消息：

```json
{
  "type": "message",
  "id": "item_legacy_invalid_id",
  "role": "assistant",
  "content": [
    {
      "type": "output_text",
      "text": "历史消息"
    }
  ]
}
```

预期：

- 接口不返回 HTTP 400；
- 非法 ID 被删除、映射或重新生成；
- 消息内容被正常传递；
- 后续采样正常完成。

### 8.5 稳定性回归测试

循环执行以下工作流至少 100 次：

```text
用户消息
→ assistant commentary
→ function call
→ function call output
→ 再次模型采样
→ final answer
```

测试期间不得出现：

```text
Expected an ID that begins with 'msg'
```

## 9. 验收标准

修复完成后应满足以下条件：

- [ ] 所有 `type: "message"` 输出项的 ID 都以 `msg_` 开头。
- [ ] 流式和非流式接口使用相同的类型与 ID 规则。
- [ ] 同一输出项在全部 SSE 事件中使用相同 ID。
- [ ] assistant commentary 与 function call 同时出现时仍可继续采样。
- [ ] 工具调用结果提交后可以正常生成最终回答。
- [ ] 历史中已有的 `item_...` message 不再导致 HTTP 400。
- [ ] 错误修复不影响 reasoning、function call 和 function call output。
- [ ] 增加覆盖真实 Codex 工作流的自动化回归测试。
- [ ] 连续稳定性测试中不再出现 message ID 前缀错误。

## 10. 建议排查位置

可以先在项目中执行：

```bash
rg -n 'item_|msg_|output_item|OutputItem|GenerateID|New.*ID' .
```

重点检查：

- Responses API response converter；
- Chat Completions 到 Responses API 的转换器；
- SSE event builder；
- output item ID generator；
- Responses API request validator；
- 工具调用后的历史消息组装逻辑；
- 上游响应透传时的 ID 保留逻辑。

排查时需要特别关注是否存在多套 message 构造器。例如：普通最终回答使用一套逻辑，而 commentary、工具调用前消息或兼容上游响应使用另一套逻辑。

## 11. 临时缓解措施

正式修复部署前，可以采用以下临时方案：

1. 在网关入站阶段删除非法 message ID。
2. 在出站阶段将缺失或非法的 message ID 重新生成为 `msg_...`。
3. 遇到已污染的 Codex 任务时，新建任务以避开旧历史。
4. 临时切换到严格兼容 Responses API 的模型提供方。

其中，第 1、2 项适合服务端快速缓解；第 3、4 项只能减少用户影响，不能消除根因。

## 12. 最终判断

本次故障的核心不是 Codex无法识别 `item_`，而是网关对同一个对象采用了互相冲突的规则：

- 出站响应允许 `type: "message"` 携带 `item_...`；
- 入站请求又要求 `type: "message"` 必须携带 `msg_...`。

正确修复应把协议一致性放在网关边界处理：所有发给客户端的 Responses API 对象都必须先完成类型和 ID 校验，同时为历史非法对象提供兼容迁移路径。完成出站修复与入站兼容后，新任务和已经污染的旧任务才能同时恢复正常。
