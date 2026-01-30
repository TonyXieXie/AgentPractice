# OpenAI API JSON Schema Guide

This document provides a detailed specification for using **JSON Schema** with the OpenAI API, specifically for **Structured Outputs** and **Function Calling**. This guide is based on the official OpenAI documentation (as of late 2024).

---

## 1. Concepts Overview

OpenAI uses JSON Schema in two primary contexts:

1.  **Structured Outputs (`response_format`)**:
    *   **Goal**: Force the model to generate a response that *strictly* adheres to a specific JSON structure.
    *   **Usage**: Used in the `chat.completions` endpoint with `response_format: { type: "json_schema", ... }`.
    *   **Strictness**: Requires `strict: true`, enforcing 100% schema compliance.

2.  **Function Calling (`tools`)**:
    *   **Goal**: Define the usage parameters for a tool/function so the model knows how to call it.
    *   **Usage**: Used in the `tools` parameter.
    *   **Strictness**: Can optionally use `strict: true` (recommended) to enforce schema compliance similarly to Structured Outputs.

---

## 2. Structured Outputs (`response_format`)

To guarantee the model output matches your schema, you must adhere to specific strict rules.

### API Payload Example

```json
{
  "model": "gpt-4o-2024-08-06",
  "messages": [
    { "role": "system", "content": "Extract data." },
    { "role": "user", "content": "..." }
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "data_extraction",
      "strict": true,
      "schema": {
        // ... Your JSON Schema Here ...
      }
    }
  }
}
```

### Critical Constraints (Strict Mode)

When `strict: true` is enabled (mandatory for valid Structured Outputs), you **MUST** follow these rules:

1.  **Root Object**: The root of the schema must be `type: "object"`.
2.  **Required Fields**: All fields defined in `properties` **MUST** be included in the `required` array. No optional fields are allowed in the traditional sense.
    *   *Workaround for optional fields*: Use a union with `null` (e.g., `type: ["string", "null"]`) to indicate a field might be missing/empty.
3.  **No Additional Properties**: All objects must set `"additionalProperties": false`.
4.  **No `anyOf` at Root**: The root object cannot use `anyOf`.
5.  **Nesting Limit**: Maximum of **5 levels** of nesting.
6.  **Property Limit**: Maximum of **100 properties** per object.

---

## 3. Supported JSON Schema Keywords

OpenAI supports a subset of the JSON Schema Draft 7 specification.

### Supported Types
*   **String**: `{"type": "string"}`
    *   *Supported keywords*: `enum`
    *   *Unsupported*: `minLength`, `maxLength`, `pattern`, `format`
*   **Number / Integer**: `{"type": "number"}` or `{"type": "integer"}`
    *   *Supported keywords*: `enum`
    *   *Unsupported*: `minimum`, `maximum`, `multipleOf`
*   **Boolean**: `{"type": "boolean"}`
*   **Null**: `{"type": "null"}`
*   **Object**: `{"type": "object"}`
    *   *Required keywords*: `properties`, `required` (must include all properties), `additionalProperties: false`
*   **Array**: `{"type": "array"}`
    *   *Required keywords*: `items`
    *   *Unsupported*: `minItems`, `maxItems`, `uniqueItems`
*   **Enum**: `{"type": "string", "enum": ["A", "B", "C"]}` (Highly recommended for classification tasks)
*   **AnyOf**: `{"anyOf": [...]}` (Supported for nested objects, but usually treated as a union type)

### Unsupported Keywords
Using these will often result in a `400 Bad Request` or the schema being rejected:
*   `default` (The API cannot "fill in" defaults for you)
*   `allOf`, `oneOf`, `not`
*   `format` (e.g., `date-time`, `email`, `uri` are ignored or unsupported)
*   `pattern` (Regex validation is not enforced)
*   Recursive schemas (self-referencing logic is generally not supported in strict mode)

---

## 4. Examples

### Example 1: Basic Object (Strict)

A schema to extract a user's name and age. Note that usually "age" might be optional, but here we must make it nullable if we want to allow it to be skipped.

```json
{
  "type": "object",
  "properties": {
    "username": {
      "type": "string",
      "description": "The user's handle"
    },
    "age": {
      "type": ["integer", "null"], 
      "description": "Age in years, null if unknown"
    },
    "status": {
      "type": "string",
      "enum": ["active", "inactive", "banned"]
    }
  },
  "required": ["username", "age", "status"],
  "additionalProperties": false
}
```

### Example 2: Array of Objects

```json
{
  "type": "object",
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "price": { "type": "number" }
        },
        "required": ["name", "price"],
        "additionalProperties": false
      }
    }
  },
  "required": ["items"],
  "additionalProperties": false
}
```

---

## 5. Python SDK (Pydantic) Usage

In Python, you rarely write raw JSON Schema. Instead, use `pydantic` models which the OpenAI SDK automatically converts.

```python
from pydantic import BaseModel
from typing import Optional

class UserInfo(BaseModel):
    username: str
    age: Optional[int] # Converts to ["integer", "null"]
    status: str

# In Function Calling or Structured Outputs
completion = client.beta.chat.completions.parse(
    model="gpt-4o-2024-08-06",
    messages=[...],
    response_format=UserInfo, 
)
```

## 6. Common Pitfalls

1.  **Missing `additionalProperties: false`**: This is the #1 error in strict mode. Every object definition needs this.
2.  **Implicit Optionals**: In standard JSON Schema, if a field isn't in `required`, it's optional. in **OpenAI Strict Mode**, *every* field must be in `required`. If you want it optional, use a nullable type.
3.  **Undefined Enums**: If you use `enum`, ensure the model's possible outputs are covered.
4.  **Docstrings**: Always write meaningful `description` fields (or docstrings in Pydantic/Python). The model reads these to understand *what* to put in the field.
