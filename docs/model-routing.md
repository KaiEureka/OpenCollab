# Shared model routing and request capacity

[中文](model-routing.zh-CN.md)

A logical model name selects an explicit backend through `model_routes`. Each
route supplies the physical model identifier, provider, endpoint and credentials.
The runtime resolves the route before selecting model capabilities, reasoning
controls, token estimators and context limits. Requests and provider usage records
keep the physical model identifier. `OpenCollab.configuration` exposes the logical
to physical mapping alongside provider, protocol, context and capacity metadata.

```python
import os
from pathlib import Path

from opencollab import OpenCollab

pool = Path(os.environ["MODEL_REQUEST_POOL"]).resolve()
pool.mkdir(parents=True, exist_ok=True)

client = OpenCollab(
    ".",
    model="worker",
    model_routes={
        "worker": {
            "model": "served-model-id",
            "provider": "openai",
            "wire_protocol": "chat_completions",
            "base_url": os.environ["MODEL_BASE_URL"],
            "api_key_env": "MODEL_API_KEY",  # pragma: allowlist secret
            "context_window": 131072,
            "request_pool": str(pool),
            "request_capacity": 2,
        },
    },
)
```

The same mapping is accepted through `config={"model_routes": ...}`. CLI and SDK
configuration also accept `OPENCOLLAB_MODEL_ROUTES` as a JSON object in the process
environment or a [configuration env file](../configs/README.md). SDK constructor
arguments take precedence. A Team role's `model` selects either a logical route or
an ordinary physical model. Names absent from `model_routes` retain the existing
default provider configuration. A route resolves once to its declared physical
model, including when that identifier is also another logical name.

`provider` defaults to `openai` and `wire_protocol` to `chat_completions`. Native
Anthropic routes use `provider="anthropic"`. Route credentials follow the existing
provider and endpoint credential selection rules. `api_key_env` selects a specific
secret from the environment or selected env file and raises an error if unset.
An explicit route `api_key` takes precedence. `auth_token` supports native
Anthropic bearer authentication. Each route owns its endpoint credentials.
`OpenCollab(...)` resolves env-file credentials at construction time. Explicit
route `context_window` values apply to the physical model. Sampling, thinking,
retry, timeout and output settings continue through the ordinary provider path.
Responses requests retain their response items, provider identity and optional
`response_session_id` handling.

`request_pool` and `request_capacity` are supplied together. The pool is an
existing absolute local directory shared by all participating processes. The
runtime verifies the directory before acquiring a request slot. Its slot files
hold OS locks until native completion or cancellation cleanup finishes. Queued
cancellation releases only the waiter's resources. A terminated process releases
its OS locks. Admission covers provider retries and releases before tools execute,
so a parent waiting for a child leaves request capacity available to that child.
The pool may be configured at the top level with `OPENCOLLAB_REQUEST_POOL` and
`OPENCOLLAB_REQUEST_CAPACITY`, or the corresponding SDK configuration fields.
A route-specific pool pair overrides that shared default. Multiple routes can
share one pool when their requests consume the same backend capacity.

The pool's `capacity` file records its configured size. Conflicting capacities
raise an error because independent processes using different slot counts can
oversubscribe the same backend. OS locks serialize that configuration decision.
Keep the directory and its lock files in place while any participating process
is running. To change capacity, stop those processes, empty the pool directory,
then restart them with the new value. A local filesystem with working OS locks is
required. Network filesystem locking and machines with separate filesystems
require admission at the backend or gateway.

A container must mount the same host directory at the configured absolute path.
A missing directory produces an error explaining the mount requirement. Containers
with independently created directories have independent capacity, even when the
path strings match. Pool paths belong to the process running the Python model
client, which may differ from its tools' execution environment.

For multiple hosts, configure every route's `base_url` to the same externally
managed gateway and configure its backend request capacity there. The gateway
receives the route's physical model identifier through the existing provider wire
protocol and owns admission across hosts. Gateway protocol support must cover
the selected Chat Completions, Responses or Anthropic Messages path and preserve
model identity, reasoning controls and continuation data. Backend work that
continues after an HTTP disconnect is governed by the backend or gateway's
cancellation policy. Local request pools coordinate client request lifetimes on
one host.
