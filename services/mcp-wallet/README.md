# Wallet MCP server (owner: Timmy)

Gives any MCP-capable agent (Claude Desktop, Claude Code, ChatGPT connectors, Abdullah's agent) a wallet it
cannot misuse. It is a thin client over the wallet HTTP API in `contracts/openapi.json`: every policy check,
reservation and debit still happens in `services/api/mandate/payments`.

| Tool | Calls | Notes |
|---|---|---|
| `get_catalog` | `GET /catalog` | Served once the catalog route lands (Abdullah); 404 until then |
| `get_mandate` | `GET /mandates/{id}` | Read-only |
| `get_budget` | `GET /wallet/{id}` | Read-only |
| `create_quote` / `get_quote` | `POST /quotes`, `GET /quotes/{id}` | Prices come from the trusted adapter |
| `get_payment_options` | `GET /quotes/{id}/payment-options` | Routes ranked by net cost after observed rewards |
| `authorize_purchase` | `POST /authorizations` | `refused` and `requires_review` are results, not errors; call again with the same `transaction_id` to pick up the owner's answer |
| `get_approval` | `GET /approvals/{id}` | Whether the owner approved, denied or let it lapse |
| `pay` | `POST /payments` | Takes only `transaction_id`; replays return the same receipt |
| `get_receipt` | `GET /payments/{txn}` | Read-only |
| `cancel_reservation` | `POST /reservations/{id}/cancel` | Releases reserved money |

**Not exposed:** confirming, drafting and revoking mandates, approving purchases and refunds. Those are user-only, so the agent cannot grant or
widen its own authority. The server runs with an agent token, so the API would refuse them anyway (HTTP 403).

**The signed authorization token never reaches the model.** The server holds it in memory by transaction ID
and attaches it on `pay`. If the server restarts between the two calls, calling `authorize_purchase` again with
the same `transaction_id` replays the original decision without a second reservation. Idempotency keys are
derived from the transaction ID, so retries are always safe.

## Run

```bash
pip install -r services/mcp-wallet/requirements.txt

# terminal 1: the wallet dev API
cd services/api && uvicorn --factory mandate.payments.dev_app:seeded_app --port 8000

# terminal 2: the MCP server (stdio by default; --transport streamable-http --port 8765 for HTTP at /mcp)
cd services/mcp-wallet && MANDATE_AGENT_TOKEN=dev-agent-token python3 -m wallet_mcp
```

`MANDATE_API_URL` defaults to `http://localhost:8000/api/v1`. A mandate has to be confirmed by the user first
(through the web app or `POST /mandates/confirm` with the user token), and its ID handed to the agent.

## Connect from Claude Desktop

Add this to `claude_desktop_config.json` (Settings, Developer, Edit Config), using absolute paths:

```json
{
  "mcpServers": {
    "mandate-wallet": {
      "command": "python3",
      "args": ["-m", "wallet_mcp"],
      "cwd": "/absolute/path/to/hacku-2026/services/mcp-wallet",
      "env": {
        "MANDATE_AGENT_TOKEN": "dev-agent-token",
        "MANDATE_API_URL": "http://localhost:8000/api/v1"
      }
    }
  }
}
```

If your Claude Desktop ignores `cwd`, add `"PYTHONPATH": "/absolute/path/to/hacku-2026/services/mcp-wallet"`
to `env`. For Claude Code: `claude mcp add mandate-wallet -e MANDATE_AGENT_TOKEN=dev-agent-token -- python3 -m wallet_mcp`
from this folder.

Then ask: *"Using mandate m_..., buy 3 bags of jasmine rice (p_a_rice) from demo_store_a with standard delivery
(ctx_a_standard)."* Ask for 4 bags to see a rule-specific refusal.

## Test

```bash
cd services/mcp-wallet && python3 -m pytest -q
```

The smoke test opens a real MCP client session against the server, backed by the wallet dev app in-process:
it checks the tool list, a quote, authorize, pay and replay, a refusal and error mapping.
