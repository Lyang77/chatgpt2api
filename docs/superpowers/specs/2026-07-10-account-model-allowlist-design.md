# Account Model Allowlist

## Goal

Allow an administrator to assign a strict set of model IDs to each account in
the account pool. A request may only use an account whose allowlist contains
the requested model. The restriction applies to every text, image, Responses,
Anthropic, and search request path.

## Data Contract

Each account gains an `allowed_models: string[]` field in the existing account
JSON storage. Values are trimmed, lowercased, deduplicated model IDs.

- An empty list means the account is unrestricted, preserving existing
  accounts and imports.
- A non-empty list is an exact allowlist. Model aliases, plan-prefixed image
  model IDs, and `auto` are separate values.
- `auto` must not select an account with a non-empty allowlist. It may only
  use unrestricted accounts, so a restricted account cannot be bypassed.

## Routing

The account service owns `account_allows_model(account, model)` and applies it
inside both text and image candidate selection.

- Text protocols pass the requested model through initial selection and retry
  selection.
- Image selection combines model allowlist filtering with existing source and
  plan-type filtering.
- Web search passes its fixed runtime model through text selection.
- If no active account matches, the request fails with
  `no available account supports model <model>` and does not silently choose a
  different model or account.

## Administration UI

The account update API accepts an optional `allowed_models` array. The account
edit dialog edits the allowlist as model IDs and the account list displays the
configured set or an unrestricted state. Imported account payloads retain the
field when present; refresh and token rotation preserve it.

## Compatibility And Verification

Existing accounts normalize to an empty allowlist and continue serving any
model. Tests cover text and image account filtering, retry filtering, API
normalization, no-match errors, and the account edit request contract.
