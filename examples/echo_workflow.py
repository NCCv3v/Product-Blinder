"""Trusted local adapter contract: JSON on stdin -> JSON on stdout. No LLM involved."""
import json
import sys
request = json.load(sys.stdin)
print(json.dumps({"output": "Echo: " + request["input"]}))
