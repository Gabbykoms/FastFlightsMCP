# FastFlightsMCP: CHECKS.md

## Week 9

### Shared server scaffold

- Tests-first commit: `08e8276`; five collected cases initially errored because
  the application package did not exist.
- After adding the shared application and root launcher:
  `.venv/bin/python -m pytest tests/test_server_scaffold.py -q` — 5 passed.
- Verified in-memory MCP discovery, default stdio dispatch, loopback HTTP
  dispatch, root-launcher delegation, and rejection of unknown CLI arguments.
- No flight tools are registered yet. Transport dispatch tests mock startup;
  they do not prove live HTTP or subprocess connectivity.
- Gabriel approved setuptools as the build backend. Built the development wheel
  `fastflights_mcp-0.1.0.dev0-py3-none-any.whl` with `pip wheel . --no-deps`.
- Verified wheel contents and console entry point. Normal wheel installation,
  `fastflights-mcp --help`, and `python server.py --help` succeed.
- Re-ran all five scaffold tests against the installed package without a manual
  source-path override: 5 passed. No PyPI upload was performed.
- Editable installation built successfully, but this environment did not include
  the `.pth` source path at runtime. Use a normal installation here for now;
  editable-install compatibility remains to be investigated. This verification
  reused existing dependencies, not a fully clean external environment.

### Tools
| Tool | Tests | What happened when Claude Code called it |
|---|---|---|
| | | |

### Output of `client.py`
```text
```

### The stray `print` error
```text
```

### Server or skill?
<!-- One paragraph: why this task needs a server, and one piece of it that could have
     been a script or a skill instead, with the reason for the choice. -->

### The gate
<!-- For the tool that changes something: what the server does, what the host did, and
     what a person still has to do. If there is no such tool yet, which TP2 tool is first. -->
