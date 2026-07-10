# Image subtask logs, in-flight query, and local stop

## Goal

Make one image-generation log represent one actual generated image subtask. The log is created when the subtask starts, remains queryable while it is running, and is updated in place when it succeeds, fails, or is manually stopped.

This lets account management show the exact running work for an account, including its prompt and input images, while the log-management page can filter, inspect, and stop a specific running image subtask.

## Scope

- Apply to image-generation paths that use the account image pool.
- One request that asks for `n` images produces `n` subtask logs.
- Add a shared batch ID to associate sibling subtasks from the same incoming API request.
- Persist the prompt and input-image URLs in each subtask log.
- Add `running` and `stopped` statuses and update the same record through its lifecycle.
- Add running-task query and a stop action in log management.
- Add account-management in-flight detail backed by running subtask logs.

Out of scope:

- A parent/summary log for a whole multi-image API request.
- Calling an upstream cancellation API or guaranteeing that upstream computation stops.
- Historical task orchestration beyond the normal retained system logs.

## Data model

The existing `system_log` table remains the persistent store. Each image subtask uses one `call` log record with these detail fields in addition to the current common log fields:

| Field | Meaning |
| --- | --- |
| `status` | `running`, `success`, `failed`, or `stopped` |
| `batch_id` | Unique ID shared by every image requested in one API call |
| `image_index` | One-based image position in the batch |
| `image_total` | Total requested images in the batch |
| `stage` | `getting_account`, `generating`, `polling`, or a terminal stage |
| `retry_count` | Attempts made for this subtask |
| `stop_requested_at` | Timestamp at which an administrator requested local cancellation |
| `stopped_at` | Timestamp at which the worker observed the cancellation |

Each record also stores the full whitespace-normalized request prompt and persisted input-image URLs. The log continues to contain its associated account email, model, timing, output images, conversation ID when available, and error when relevant.

The storage layer gains explicit create and update operations. Updating a log must update both `detail_json` and the denormalized `status`, `account_email`, and `key_name` columns so existing filters and indexes remain accurate.

## Lifecycle

```text
incoming image request
  -> create one running log per requested image, sharing batch_id
  -> select account and update its log with account_email/stage
  -> generate, poll, and update stage/retry_count
  -> update same log to success, failed, or stopped
```

For a retry that moves to another account, the same subtask log remains the source of truth. Its retry count increases and the account/stage fields are updated to the currently active attempt. The account pool still owns its in-memory slot counter for scheduling; logs are the durable observability source, not the scheduler's concurrency primitive.

## Local stop semantics

Stopping a log is a local, cooperative cancellation:

1. The stop endpoint only accepts a currently `running` image-subtask log.
2. It records `stop_requested_at` and marks the task's cancellation signal.
3. The worker checks that signal before account acquisition, between retries and retry waits, and during streaming or polling boundaries.
4. Once observed, the worker stops local processing, releases its account slot in `finally`, and updates that same log to `stopped` with terminal timestamps.
5. If upstream finishes after local cancellation, its output is discarded and is not returned to the API caller or saved as a result image.

Stopping one log affects only that image subtask. Sibling images in the same batch continue normally. Repeating a stop request is idempotent: terminal logs remain unchanged and a currently stopping task does not receive duplicate cleanup.

## APIs and UI

- Extend the existing log list query with `running` and `stopped` status support plus model, endpoint, batch ID, account email, key/caller, summary, and date-range filters.
- Add an administrator-only stop endpoint addressed by log ID. It returns the current log state and reports whether a cancellation signal was newly set.
- Expose an account in-flight-detail endpoint that returns only `running` image-subtask logs for the selected account. It returns the prompt and input-image URLs needed for the detail dialog.
- In account management, make a positive in-flight count clickable. The dialog refreshes while open and shows model, image index/total, elapsed time, stage, retry count, prompt, and input-image previews.
- In log management, add status labels for `running` and `stopped`, the new filters, and a stop button only for `running` image-subtask logs. The UI refreshes after a stop request.

All log-management and account-management detail/stop endpoints remain administrator-only.

## Error handling and consistency

- Log creation happens before the image worker starts, so a task waiting for an account is visible as running.
- Every terminal and exception path updates the same log and releases a held account slot exactly once.
- If a request is cancelled before an account is selected, its log still becomes `stopped`; no account email is required.
- If a process terminates, no worker remains to complete the task. Recovery treatment of stale `running` logs is intentionally excluded from this change; operationally they represent work interrupted by the process.
- Failure to persist a nonessential progress update must not leave a slot held. Terminal status and slot release are protected by `finally` paths.

## Verification

- Unit-test log create/update, including indexed filter columns changing after an update.
- Test one request with multiple images creates distinct logs with one shared batch ID.
- Test account assignment, retry/account change, success, failure, and polling timeout update the intended log only.
- Test stop before account assignment, during retry wait, and while an account slot is held; assert no result is emitted and the slot is released.
- Test duplicate stop requests are idempotent.
- Test log and account-management APIs enforce admin authorization and return only running rows for in-flight detail.
- Add frontend tests for new status/filter behavior and for stop-button visibility/refresh.
