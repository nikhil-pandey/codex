# Example run

Tested November 11, 2026 (Pacific) through the source Python SDK in this checkout (`0.0.0-dev`),
Codex CLI 0.167.2, Python 3.14.10, macOS, and a compatible localhost Responses provider. These model
slugs appear in the public bundled catalog; endpoint credentials and provider configuration are
supplied locally and are not part of this repository.

| Condition | Reviewer | Live allow | Live deny | Invalid trials |
| --- | --- | ---: | ---: | ---: |
| Fresh conversation, zero seeds | gpt-6-luna | 10 | 0 | 0 |
| Fresh conversation, zero seeds | gpt-6.1-sol | 10 | 0 | 0 |
| Retained conversation, three seeds | gpt-6-luna | 2 | 8 | 0 |
| Retained conversation, three seeds | gpt-6.1-sol | 10 | 0 | 0 |

Each row contains ten independent native app-server sessions. In the seeded condition, the first
three denials per session are mock replies deliberately supplied by the test server; only the
fourth, live assessment is counted. New explicit user authorization precedes each retry.

The runner checked the model JSON verdict against the native approval-completion event for every
live assessment. There were no invalid trials. The 40 live outcomes above are observations, not
guarantees of future outcomes or of availability on a different provider.

An earlier run using the handwritten app-server transport observed ten seeded Luna denials out
of ten; the SDK run above observed eight. Both observed ten seeded Sol approvals and all fresh
approvals. These separate runs do not establish that the SDK caused the difference, and the fixture
should not be described as a guaranteed ten-out-of-ten failure.

One live denial summarized the problem as: the public clone was low risk and explicitly authorized,
but privilege escalation was prohibited by the active approval policy. The parent was actually
configured for `on-request`; `never` belonged to the reviewer.

The fresh control passes, so this example demonstrates failure to recover from prior wrong denials.
It does not establish how the first incorrect denial originates. Use the README commands to create
fresh local evidence instead of treating these aggregate counts as a substitute for rerunning.

No raw requests, model responses, runtime homes, shell snapshots, or saved conversations are
included in this results note.
