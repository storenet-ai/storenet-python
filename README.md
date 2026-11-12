# storenet

Python library and `storenet` command-line tool for the StoreNet Partner API: create, edit with
AI, and list the web designs of a StoreNet shop without signing HMAC requests yourself.

- Python 3.10 or later, standard library only (no dependencies).
- Version 0.1 covers three operations on shop designs: `create`, `update`, `list`.

## Requirements

- The StoreNet shop is on the **Pro plan or higher** and has the quick shop designer turned on.
  A lower plan returns `403290`; a disabled designer returns `404310`.
- A Partner API key with the `shop_designs:write` scope, created by a shop administrator.
  Designs belong to the administrator who created the key, and AI credits are charged to that
  administrator's wallet.

## Installation

```bash
pip install storenet
storenet --version
```

## Configuration

The package reads three environment variables. If any is missing, the command exits with code 2
and names the missing variable. There is no default URL.

```bash
export STORENET_BASE_URL="https://shop.example.com"   # your shop's address
export STORENET_KEY_ID="<key id>"
export STORENET_SECRET="<secret>"
```

`STORENET_BASE_URL` must use https; plain http is accepted only for a loopback IP address on
your own machine. The package does not follow redirects (3xx): set the shop's final https
address.

Treat `STORENET_SECRET` like a password: never commit it or paste it into an issue. The package
never prints the secret or includes it in error messages.

## Command-line usage

### Create a design

```bash
storenet site create "A small bakery in the old quarter, warm cream colours"
storenet site create --file description.txt
```

Prints the design name, its industry, its ID and a public preview link. A create takes about
15 seconds. The description must be 1 to 600 characters long.

### Edit a design with AI

```bash
storenet site update 1b2c3d4e "Switch to a red colour scheme and make the title larger"
```

Prints what the AI changed, what it could not do (with reasons), the credits charged and the
remaining balance, and the preview link.

- `ID` is the full ID or its first few characters (usually the 8 shown by `site list`). A prefix
  that matches several designs lists the matching IDs and exits with code 2; a prefix that
  matches none exits with code 1.
- The request must be 1 to 400 characters long. An edit can take up to about 110 seconds.
- When the AI applies no change, the command prints the reasons, charges no credit and exits
  with code 1.
- The preview link stays the same after an edit.

### List designs

```bash
storenet site list
```

Prints a table with the columns ID (first 8 characters), name, industry, last update and link,
followed by the total. The list contains only the designs created through the Partner API by
the key's owner, most recently updated first; the command fetches every page.

### Common options

- `--json`: print the server's raw JSON payload (for scripts).
- `--idempotency-key KEY` (`create`, `update`): set your own deduplication key. Sending the same
  key with the same body again returns the original result, without creating anything new or
  charging credits a second time.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The server returned an error, the AI changed nothing, or the ID prefix matched no design |
| 2 | Invalid usage, a missing or invalid environment variable, or an ID prefix that matched several designs |

## Library usage

```python
from storenet import StorenetClient, StorenetError

client = StorenetClient.from_env()  # or StorenetClient(base_url, key_id, secret)

site = client.sites.create("A pet shop with a green colour scheme")
print(site.id, site.name, site.industry, site.url)

edit = client.sites.update(site.id, "Switch to an orange colour scheme")
if edit.applied:
    print(edit.changed, edit.credits_used, edit.credits_available)
else:
    print(edit.outcome, edit.refused, edit.unsupported)

for item in client.sites.list():
    print(item.id[:8], item.name, item.updated_at)
```

- `sites.update` returns an `Edit` even when the AI changes nothing (`outcome` is `refused` or
  `empty`); only real errors raise `StorenetError`.
- `sites.update` needs the full ID (a UUID) as in `Site.id`; any other value raises `ValueError`
  before anything is sent. Only the `storenet` command resolves an ID from its first characters.
- `StorenetClient(..., timeout=130, max_retries=2)`: the timeout applies to each request.

## Automatic retries

Every `create` / `update` sends an `Idempotency-Key` (generated if you do not pass one). On a
temporary error the package retries up to `max_retries` times (default 2) with the **same** key
and a fresh signature:

- `429`: waits for the `Retry-After` header (at most 60 seconds); without the header it waits
  2, then 4 seconds.
- `503310`, `503190`, `409171`, a timeout or a dropped connection: waits 2, then 4 seconds.
- `409310` (the design was just saved elsewhere) on `update`: retries exactly once against the
  latest version.

Other 4xx errors (for example `403080`, out of credits) are not retried.

When `create` / `update` still fails after every retry, `StorenetError.details["idempotency_key"]`
holds the key that was sent. The request may have completed on the server: call again with that
same key to get the result without being charged again. The `storenet` command prints an
`--idempotency-key <key>` hint for you.

## Errors

Errors returned by the server carry the server's code in `StorenetError.code` (for example
`"409310"`), together with `status`, `message` and `details`. Client-side errors use text codes:

| Code | When |
|---|---|
| `missing_config` | A missing environment variable, or a `base_url` that is not a full URL (`StorenetConfigError`) |
| `insecure_base_url` | `base_url` uses http to a host other than a loopback IP address (`StorenetConfigError`) |
| `redirected` | The server answered with a 3xx redirect; `details["location"]` is the target. Set `STORENET_BASE_URL` to the final https address |
| `not_deployed` | The shop's server does not support this operation yet (usually `list` / `update`); ask the shop administrator to update |
| `validation_error` | The server rejected the request data (422) |
| `timeout` | No response within the timeout |
| `network_error` | Could not connect, or the connection dropped |
| `invalid_response` | The server returned something that is not JSON or not in the expected format |

Error messages that come from the server may be in Vietnamese.

## Security

Report vulnerabilities as described in
[SECURITY.md](https://github.com/storenet-ai/storenet-python/blob/main/SECURITY.md), not in a
public issue.

## License

MIT.
