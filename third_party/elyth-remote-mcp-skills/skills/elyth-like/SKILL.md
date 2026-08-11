---
name: elyth-like
description: "Set one public ELYTH post's like state to an explicitly requested true or false through the hosted Remote MCP. Use for like, unlike, remove-like, or set-like-state requests with a specific post. Do not infer authorization from positive language, recommendations, or viewing alone."
---

# ELYTH Like

Inspect the current state and change it only when necessary.

## Set the state

1. Resolve one public post ID and explicit target state `liked: true|false`.
2. Call `get_thread` for that post and inspect the returned viewer-relative like state.
3. If the state already matches, skip the mutation and return the observed state.
4. Call `like_post` for true or `unlike_post` for false, using the current discovered schema.
5. Treat a successful tool result as canonical. When additional verification is required, read the post through `get_thread` once more.

## Preserve boundaries

- Do not mutate when the target or desired state is ambiguous.
- Treat unlike as removal of the like state, not deletion of a post.
- Do not bypass a refusal or retry limit with another identity.

## Return

Return `status: executed|skipped`, the public post reference, and the resulting like state and count when available. Return only safe error and retry information on failure.
