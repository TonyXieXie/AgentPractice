# Comprehensive OpenAI API Guide

This guide details the core specifications for the OpenAI API, focusing on the **Chat Completions** endpoint, **JSON Schema (Structured Outputs)**, and **Function Calling**. It incorporates the latest features as of late 2024/early 2025 (e.g., O1 models, Developer role).

---

## 1. Chat Completions API
**Endpoint**: `POST https://api.openai.com/v1/chat/completions`

### 1.1 Core Request Parameters

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `model` | string | **Yes** | ID of the model (e.g., `gpt-4o`, `gpt-4o-mini`, `o1-preview`). |
| `messages` | array | **Yes** | A list of messages comprising the conversation so far. |
| `store` | boolean | No | Whether to store the output for model distillation/evals (default: `false`). |
| `reasoning_effort`| string | No | **(O1 models only)** Constrains reasoning depth. Values: `low`, `medium`, `high`. |
| `metadata` | object | No | Developer-defined tags for the request (useful for filtering in dashboard). |
| `frequency_penalty`| number | No | Number between -2.0 and 2.0. Positive values penalize new tokens based on their existing frequency in the text so far. |
| `logit_bias` | map | No | Modify the likelihood of specified tokens appearing in the completion. |
| `logprobs` | boolean | No | Whether to return log probabilities of the output tokens. |
| `top_logprobs` | integer | No | An integer between 0 and 20 specifying the number of most likely tokens to return at each position. |
| `max_completion_tokens`| integer | No | Upper bound for generated tokens. Replaces the deprecated `max_tokens` for newer models. |
| `n` | integer | No | How many chat completion choices to generate for each input message. |
| `presence_penalty` | number | No | Number between -2.0 and 2.0. Positive values penalize new tokens based on whether they appear in the text so far. |
| `response_format` | object | No | Specifies the output format (e.g., `{ "type": "json_object" }` or `{ "type": "json_schema", ... }`). |
| `seed` | integer | No | If specified, the system will make a best effort to sample deterministically. |
| `service_tier` | string | No | Latency tier to use (`auto` or `default`). |
| `stop` | string/array| No | Up to 4 sequences where the API will stop generating further tokens. |
| `stream` | boolean | No | If set, partial message deltas will be sent. |
| `stream_options` | object | No | Options for streaming responses (e.g. `{ "include_usage": true }`). |
| `temperature` | number | No | Sampling temperature (0 to 2). Higher = more random. |
| `top_p` | number | No | Nucleus sampling. Alternative to temperature. |
| `tools` | array | No | A list of tools the model may call. |
| `tool_choice` | string/obj| No | Controls which (if any) tool is called. |
| `parallel_tool_calls`| boolean | No | Whether to enable parallel function calling (default: `true`). |
| `user` | string | No | A unique identifier representing your end-user. |

> **Note on O-Series Models (`o1`, `o3-mini`)**: These models **do not** support `temperature`, `top_p`, or `presence_penalty` when `reasoning_effort` is dominant. The `system` role is often replaced by or mapped to the `developer` role.

### 1.2 Message Roles

*   **`system`**: Sets the behavior/persona of the assistant. (Legacy/Standard models).
*   **`developer`**: (New, O-Series favored) Critical instructions that take precedence over user instructions. Replaces `system` for reasoning models.
*   **`user`**: The end-user's input.
*   **`assistant`**: The model's response (or pre-filled examples). Can contain `content` (text) or `tool_calls`.
*   **`tool`**: The result of a function call. Must include `tool_call_id`.

---

## 2. Structured Outputs & JSON Schema
**Structured Outputs** guarantees the model generates JSON matching your schema.

### 2.1 Usage
Use `response_format` with `type: "json_schema"`.

```json
"response_format": {
    "type": "json_schema",
    "json_schema": {
        "name": "my_schema",
        "strict": true,
        "schema": { ... }
    }
}
```

### 2.2 Strict Mode Rules
When `strict: true` (required for reliability):
1.  **All fields must be required**: `required` array must list every property.
2.  **No extra properties**: `additionalProperties: false` is mandatory on all objects.
3.  **Data Types**:
    *   **Supported**: `string`, `number`, `integer`, `boolean`, `object`, `array`, `enum`, `null`.
    *   **Unsupported**: `oneOf`, `allOf`, `minItems`, `maxItems`, `pattern`, `format` (e.g. email/date).
4.  **Root Object**: Must be `type: "object"`.
5.  **Nesting**: Max 5 levels.

### 2.3 Handling Optional Fields
Since all fields must be `required`, use a union with `null` for optionals:
```json
"email": { "type": ["string", "null"] }
```

---

## 3. Function Calling (Tools)
Allows the model to "call" functions you define.

### 3.1 Definition
Defined in the `tools` array.

```json
"tools": [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {
            "type": "object",
            "properties": {
                "location": { "type": "string" }
            },
            "required": ["location"],
            "additionalProperties": false
        },
        "strict": true // Recommended
    }
}]
```

### 3.2 Workflow
1.  **Request**: Send prompt with `tools`.
2.  **Response**: Model returns `tool_calls` array (id, name, arguments).
3.  **Execute**: Your code runs the function.
4.  **Reply**: Send a new message with `role: "tool"`, `tool_call_id`, and the function result content.

---

---

## 4. Responses API (New 2025)
**Endpoint**: `POST https://api.openai.com/v1/responses` (or `v2`)

The Responses API is a unified, stateful interface designed for Agentic applications. It supersedes parts of Chat Completions/Assistants by providing built-in server-side tools and simplified context management.

### 4.1 Key Differences vs Chat Completions

| Feature | Chat Completions | Responses API |
| :--- | :--- | :--- |
| **State** | Stateless (You manage `messages`) | Semi-stateful (Can use `store: true`) |
| **Tools** | Custom functions only | **Built-in** (Web Search, File Search) + Custom |
| **System Prompt** | `system` role message | `instructions` top-level parameter |
| **Input** | `messages` array | `input` (Text, Images, Files mix) |

### 4.2 Endpoint Parameters

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `model` | string | ID of the model (e.g., `gpt-4o-2025-xx`). |
| `input` | string/array | The user's input prompt (supports multimodal). |
| `instructions`| string | System-level instructions for the agent's behavior. |
| `tools` | array | List of tools. Supports **built-in strings**: `"web_search"`, `"file_search"`, `"computer_use"`. |
| `reasoning_effort`| string | (O-Series) Control reasoning depth (`low`, `medium`, `high`). |
| `store` | boolean | If `true`, maintains context for multi-turn interactions. |
| `response_format`| object | Same as Chat Completions (supports JSON Schema). |

### 4.3 Python SDK Example

```python
response = client.responses.create(
    model="gpt-4o",
    instructions="You are a helpful research assistant.",
    input="Find the latest specs for the OpenAI Responses API.",
    tools=[
        "web_search",  # Built-in tool! No schema needed.
        "file_search"
    ]
)
print(response.output_text)
```

---

## 5. Embeddings API
**Endpoint**: `POST https://api.openai.com/v1/embeddings`

*   **`input`**: Text to embed.
*   **`model`**: e.g., `text-embedding-3-small` or `text-embedding-3-large`.
*   **`dimensions`**: (Optional) Reduce output vector size (e.g., 256).

---

## 6. Best Practices
*   **Error Handling**: Handle 429 (Rate Limit) with exponential backoff.
*   **Token Management**: Use `max_completion_tokens` to prevent runaways.
*   **Security**: Never expose API keys on the client side.
*   **Latency**: Use `stream: true` for perceived speed.
