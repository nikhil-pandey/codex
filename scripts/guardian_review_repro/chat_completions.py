"""Adapt the fixture's native reviewer request to a Chat Completions provider.

This changes the wire format, so these runs must be reported separately from native Responses
runs. Visible messages, tool descriptions, the verdict schema, and reasoning effort are retained.
Custom tools use a JSON function with one `input` string; tool results return to the native harness.
The JSON format contract is repeated as an instruction; no approval policy, prior verdict, or user
authorization is added. Prose accompanying tool calls is omitted from native responses.
"""

import json


def tool_name(tool, namespace=None):
    return namespace + "__" + tool["name"] if namespace else tool["name"]


def convert_tools(body):
    """Flatten namespaces and remember how to restore each native tool call."""
    offered = list(body.get("tools", []))
    for item in body.get("input", []):
        if item.get("type") == "additional_tools":
            offered.extend(item["tools"])

    functions = []
    originals = {}
    for outer in offered:
        namespace = outer["name"] if outer.get("type") == "namespace" else None
        for tool in outer["tools"] if namespace else [outer]:
            name = tool_name(tool, namespace)
            original = {**tool, "namespace": namespace}
            originals[name] = original
            if tool["type"] == "custom":
                parameters = {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                    "additionalProperties": False,
                }
            elif tool["type"] == "function":
                parameters = tool["parameters"]
            else:
                raise ValueError("Unsupported Chat Completions tool: " + tool["type"])
            functions.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": parameters,
                    },
                }
            )
    return functions, originals


def convert_messages(body):
    """Keep the visible native conversation in order, including all injected denials."""
    messages = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": body["instructions"]})
    for item in body.get("input", []):
        kind = item.get("type", "message")
        if kind == "additional_tools":
            continue  # Moved into the request's tools field.
        if kind == "message":
            content = item.get("content", [])
            if isinstance(content, list):
                text = []
                for block in content:
                    if block.get("type") not in ("input_text", "output_text"):
                        raise ValueError(
                            "Only text reviewer input is supported by this adapter"
                        )
                    text.append(block["text"])
                content = "\n".join(text)
            messages.append({"role": item["role"], "content": content})
        elif kind in ("function_call", "custom_tool_call"):
            arguments = item.get("arguments", "{}")
            if kind == "custom_tool_call":
                arguments = json.dumps({"input": item["input"]})
            call = {
                "id": item["call_id"],
                "type": "function",
                "function": {
                    "name": tool_name(item, item.get("namespace")),
                    "arguments": arguments,
                },
            }
            if (
                messages
                and messages[-1]["role"] == "assistant"
                and "tool_calls" in messages[-1]
            ):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append(
                    {"role": "assistant", "content": None, "tool_calls": [call]}
                )
        elif kind in ("function_call_output", "custom_tool_call_output"):
            output = item["output"]
            if not isinstance(output, str):
                output = json.dumps(output)
            messages.append(
                {"role": "tool", "tool_call_id": item["call_id"], "content": output}
            )
        elif kind == "reasoning":
            # Provider-specific encrypted reasoning has no portable Chat Completions channel.
            continue
        else:
            raise ValueError("Unsupported native conversation item: " + kind)
    return messages


def chat_request(body, stream=False):
    tools, originals = convert_tools(body)
    request = {
        "model": body["model"],
        "messages": convert_messages(body),
        "stream": stream,
    }
    if tools:
        request["tools"] = tools
        request["tool_choice"] = body.get("tool_choice", "auto")
    effort = body.get("reasoning", {}).get("effort")
    if effort:
        request["reasoning_effort"] = effort
    output_format = body.get("text", {}).get("format", {})
    if output_format.get("type") == "json_schema":
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                key: value for key, value in output_format.items() if key != "type"
            },
        }
        # Some compatible providers accept response_format but do not enforce it. Spell out
        # the same format contract as well; this adds no approval or risk policy.
        request["messages"].insert(
            0,
            {
                "role": "system",
                "content": "When your assessment is complete, return only one JSON object matching "
                "the following schema. Do not include Markdown or surrounding prose. Investigation "
                "tool calls are still allowed before the final assessment.\n"
                + json.dumps(output_format["schema"]),
            },
        )
    return request, originals


def read_completion(payload):
    """Collect Chat SSE deltas, including streamed tool-call names and arguments.

    Streaming avoids compatible endpoints that omit tool_calls from non-streaming messages.
    The unmodified stream is retained in the trial directory for diagnosis.
    """
    if payload.lstrip().startswith(b"{"):
        return json.loads(payload)
    completion = {"id": "chat-review", "choices": [], "usage": {}}
    content = []
    calls = {}
    finish_reason = None
    for line in payload.decode().splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            continue
        event = json.loads(data)
        if event.get("error"):
            raise RuntimeError("Chat stream error: " + json.dumps(event["error"]))
        if event.get("id"):
            completion["id"] = event["id"]
        if event.get("usage"):
            completion["usage"] = event["usage"]
        for choice in event.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta", {})
            if delta.get("content"):
                content.append(delta["content"])
            for fragment in delta.get("tool_calls", []):
                call = calls.setdefault(
                    fragment["index"],
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if fragment.get("id"):
                    call["id"] = fragment["id"]
                function = fragment.get("function", {})
                call["function"]["name"] += function.get("name", "")
                call["function"]["arguments"] += function.get("arguments", "")
    message = {"role": "assistant", "content": "".join(content)}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    completion["choices"] = [{"message": message, "finish_reason": finish_reason}]
    return completion


def response_events(completion, originals):
    """Return the native Responses events needed by the SDK-managed reviewer."""
    choice = completion["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Chat Completions response was truncated")
    message = choice["message"]
    response_id = completion["id"]
    events = [{"type": "response.created", "response": {"id": response_id}}]
    for call in message.get("tool_calls", []):
        function = call["function"]
        tool = originals[function["name"]]
        item = {"call_id": call["id"], "name": tool["name"]}
        if tool["namespace"]:
            item["namespace"] = tool["namespace"]
        if tool["type"] == "custom":
            item.update(
                type="custom_tool_call",
                input=json.loads(function["arguments"])["input"],
            )
        else:
            item.update(type="function_call", arguments=function["arguments"])
        events.append({"type": "response.output_item.done", "item": item})
    if message.get("content") and not message.get("tool_calls"):
        events.append(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": message["content"]}],
                },
            }
        )
    if not message.get("content") and not message.get("tool_calls"):
        raise RuntimeError(
            "Chat Completions returned no verdict or investigation tool call"
        )
    usage = completion.get("usage", {})
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            },
        }
    )
    return events
