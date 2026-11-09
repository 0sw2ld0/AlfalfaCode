---
name: pod-doctor
description: 'Diagnose a Runpod Pod that is unreachable, GPU-blind or stuck, and perform a recovery action only when the user authorizes one. Read-only until the user asks for an action.'
metadata:
  author: runpod
  version: "1.7.4"
license: Apache-2.0
---

# Pod doctor

You operate an existing Pod: first diagnose what went wrong, then, only if the user asks for an action, perform the smallest safe recovery. Diagnosis is always read-only.

Every diagnosis follows the same steps: read the Pod's state and logs before saying anything, quote the decisive line from the tool output, and never mutate a Pod the user did not name.

## Phase 1 — diagnose (always, read-only)

Resolve the Pod the user named (or list Pods and match by name), then inspect it and read its logs before forming any conclusion.
