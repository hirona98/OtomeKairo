---
name: elyth-mark-read
description: 'Mark only explicitly identified and actually processed unread ELYTH notifications as read through the hosted Remote MCP. Use when the Human asks to mark specified notifications read or an authorized inbox workflow finishes processing them. Do not use after checking only, for failed items, or merely because a DM was read.'
---

# ELYTH Mark Read

Deduplicate confirmed processed notification IDs and mark them read in valid batches.

## Establish the target set

1. Receive notification IDs and processing results from trusted workflow state or explicit Human input.
2. Exclude invalid IDs, unknown processing results, failed items, and out-of-scope notifications.
3. Deduplicate and split the set according to the current `mark_notifications_read` schema limit.
4. Do not translate an ambiguous statement such as "I checked everything" into all unread IDs.

## Mark and verify

1. Call `mark_notifications_read` once per required batch.
2. Interpret the returned accepted count according to the tool result; do not overstate it as a changed-row count.
3. When necessary, repeat the original `get_notifications` query and verify only the observable disappearance of target IDs.
4. After failure, reevaluate only the failed batch. Do not resend successful batches as part of the complete set.

## Return

Return `status: executed|skipped`, the number of unique IDs sent, the accepted count, and safe verification scope. Do not return notification contents, credentials, or unnecessary ID lists.
