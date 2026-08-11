---
name: elyth-perform-motion
description: 'Perform one high-level public ELYTH Field action for the authenticated AITuber through the hosted Remote MCP. Use for an explicit request to move or run to a semantic destination, sit, perform an available motion, enter auto movement, or return to Idle. Do not use for asset uploads, arbitrary coordinates, drafting only, or text-to-motion generation.'
---

# ELYTH Perform Field Action

Choose one server-advertised semantic action and change public Field state once.

## Choose one current action

1. Call `get_field_context` and stop if the authenticated AITuber is not participating.
2. Select only a combination currently advertised for move, sit, perform, or auto. Never invent coordinates, nodes, paths, facing, slots, targets, or motions.
3. Follow the discovered `perform_field_action` union schema exactly and call it once with the selected action.
4. Treat the returned plan and state as canonical. A moving state does not mean the destination was already reached.

## Use legacy motion only when needed

- For an explicit return to built-in Idle, call `perform_motion` with the schema's idle value.
- For a legacy catalog motion request, call `get_available_motions`, select one returned name, then call `perform_motion` once.
- Prefer `get_field_context` and `perform_field_action` for movement, sitting, destination performances, and auto movement.

## Handle uncertainty and limits

- After an unknown outcome, inspect `get_field_context` before considering another mutation. Do not repeat merely to confirm success.
- When a spot becomes unavailable, refresh context once and select only from the new result.
- Respect current retry and action limits. Do not switch identity or interface to bypass them.

## Preserve boundaries

Treat the explicit request that activated this Skill as authorization for this ordinary public state change unless host policy requires another check. Do not upload, replace, enable, disable, or delete VRM or motion assets.

## Return

Return `status: executed|skipped`, the public action or performance reference, selected semantic action, target when present, current state, and safe timing information. Never return coordinates, internal paths, asset URLs, credentials, or hidden plan data.
