#!/usr/bin/python3
"""An ACP agent for testing the Swift client over a subprocess, without a Google account."""
import json
import os
import sys

assert sys.argv[1:4] == ['acp', '--sandbox', '--process-per-session'] or sys.argv[1:] == ['acp', '--sandbox']
SESSION = "test-session"


def send(**frame):
    print(json.dumps(dict(jsonrpc="2.0", **frame)), flush=True)


def read():
    return json.loads(input())


def update(kind, session=SESSION, **fields):
    send(method="session/update", params={"sessionId": session, "update": dict(sessionUpdate=kind, **fields)})


def ask():
    send(
        id="approval",
        method="session/request_permission",
        params={
            "sessionId": SESSION,
            "toolCall": {"toolCallId": "tool-1", "title": "Test command", "rawInput": {"command": "printf test"}},
            "options": [
                {"optionId": "once", "kind": "allow_once", "name": "Allow once"},
                {"optionId": "always", "kind": "allow_always", "name": "Allow always"},
                {"optionId": "reject", "kind": "reject_once", "name": "Reject once"},
            ],
        },
    )


def run():
    init = read()
    assert init["method"] == "initialize" and init["params"]["protocolVersion"] == 1
    assert init["params"]["clientCapabilities"] == {}
    send(id=init["id"], result={"protocolVersion": 1, "agentCapabilities": {"mcpCapabilities": {"http": True}},
                               "authMethods": [{"id": "oauth-personal", "name": "Sign in with Google"}]})
    new = read()
    assert new["method"] == "session/new" and os.path.realpath(new["params"]["cwd"]) == os.getcwd()
    servers = new["params"]["mcpServers"]
    if "--process-per-session" in sys.argv:
        assert servers == [
            {"type": "http", "name": "manual", "url": "https://docs.example.test/mcp", "headers": []},
            {"name": "alfalfa-delegation", "command": "/test/alfalfa", "args": ["--delegacion-mcp", "openai/test"],
             "env": [{"name": "ALFALFA_HOME", "value": "/test/settings"}]},
        ]
    else:
        assert servers == []
    send(id=new["id"], error={"code": -32000, "message": "Authentication required"})
    auth = read()
    assert auth["method"] == "authenticate" and auth["params"]["methodId"] == "oauth-personal"
    send(id=auth["id"], result={})
    retry = read()
    assert retry["method"] == "session/new" and retry["params"] == new["params"]
    send(id=retry["id"], result={"sessionId": SESSION})

    for line in sys.stdin:
        prompt = json.loads(line)
        assert prompt["method"] == "session/prompt" and prompt["params"]["sessionId"] == SESSION
        text = prompt["params"]["prompt"][0]["text"]
        update("agent_message_chunk", "other-session", content={"type": "text", "text": "wrong session"})
        update("tool_call", toolCallId="tool-1", title="Test command", status="pending")
        send(id="fs", method="fs/write_text_file", params={"sessionId": SESSION, "path": "should-not-exist.txt",
                                                          "content": "unexpected"})
        refused = read()
        assert refused["id"] == "fs" and refused["error"]["code"] == -32601

        ask()
        answer = read()
        assert answer["id"] == "approval"
        outcome = answer["result"]["outcome"]
        if text == "cancel":
            assert outcome == {"outcome": "cancelled"}
            cancel = read()
            assert cancel == {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": SESSION}}
            send(id=prompt["id"], result={"stopReason": "cancelled"})
            continue
        assert outcome == {"outcome": "selected", "optionId": "reject" if text == "deny" else "once"}
        update("tool_call_update", toolCallId="tool-1", status="failed" if text == "deny" else "completed")
        update("agent_message_chunk", content={"type": "text", "text": "OK "})
        update("agent_message_chunk", content={"type": "text", "text": text})
        update("usage_update", used=4200, size=20000)
        send(id=prompt["id"], result={"stopReason": "end_turn"})


run()
