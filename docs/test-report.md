# Test summary report — ev-charging-testing

Structure after ISO/IEC/IEEE 29119-3. Covers the run of 16 November 2026, `evse_lab` 1.0.0.

## 1. What was run

| Pass                      | Command                            | Passed | Skipped | Messages recorded |
| --------------------------- | ------------------------------------ | -------: | --------: | ------------------: |
| OCPP 1.6, in memory       | `pytest`                           |    148 |      13 |              2333 |
| OCPP 2.0.1, in memory     | `OCPP=2.0.1 pytest`                |    102 |      59 |              2101 |
| OCPP 1.6, real WebSocket  | `LINK=websocket pytest`            |     10 |     151 |               164 |
| OCPP 2.0.1, real WebSocket| `OCPP=2.0.1 LINK=websocket pytest` |      9 |     152 |               120 |
| The visual page           | `EVIDENCE=0 pytest -m pages`       |     21 |       0 |                 — |
| **Total**                 |                                    | **290** |         |          **4718** |

Nothing failed. The skips are the design of the matrix, not gaps: a test marked `once` does not depend on the
version and runs in the 1.6 pass only; a behaviour test is skipped in a WebSocket pass because the two in-memory
passes already cover it; a test marked `v16` or `v201` belongs to one version.

Beside the suite:

- **Mutation check.** 41 of 41 planted bugs caught, in 68 seconds; the control run without a bug passed. The
  table is [`mutants-result.md`](../mutants-result.md). By area: 9 in the envelope, 8 in the two dialects, 7 in
  what is billed, 7 in the cable and the session, 6 in smart charging, 4 in the offline queue.
- **Twin check.** The JavaScript station in the visual page produced 2164 messages across 22 conversations, all
  identical to the ones the Python station produces for the same script.
- **Static checks.** `ruff check`, `ruff format --check` and `mypy --strict` over `src`, `tests` and `scripts`:
  clean.

## 2. Coverage against the plan

| Scope item from the plan      | Covered by                                                   | State |
| ------------------------------- | -------------------------------------------------------------- | ------- |
| The envelope                  | `test_envelope` (9), property-based round trip               | done  |
| Message validity              | `test_schemas` (9), including 103 messages of the two versions | done |
| A charging session            | `test_session` (15)                                          | done  |
| Authorisation                 | `test_authorization` (9)                                     | done  |
| Loss of network               | `test_offline` (10)                                          | done  |
| Smart charging                | `test_smart_charging` (14)                                   | done  |
| Orders from the back office   | `test_remote_commands` (19)                                  | done  |
| Connector status              | `test_connector_status` (7)                                  | done  |
| Robustness                    | `test_robustness` (12), Hypothesis                           | done  |
| Interoperability              | `test_interop` (3), both directions over a real WebSocket    | done  |
| The visual page               | `test_pages` (10 functions, 21 cases) and the twin check     | done  |

## 3. Findings

The item under test is this project's own simulator, so a finding is fixed rather than filed. Each one below was
found by a test that failed first. Two of them are properties of the standard, not of the code: they are recorded
because they are the kind of thing a tester has to notice on a real integration.

**EVC-1 — the same meter reading is written differently by the two versions.** Severity: major. The first run of
the suite on OCPP 2.0.1 failed schema validation on every meter value. In 1.6 a sampled value carries the number
as a **string** with a sibling `unit` field; in 2.0.1 it is a **number** with a nested
`unitOfMeasure: {unit: …}` object. Each version's schema rejects the other version's shape. Fixed by making the
sampled value version-aware; pinned by `test_the_two_versions_disagree_about_a_meter_reading`, which asserts the
rejection in both directions so the two shapes can never be merged by accident.

**EVC-2 — neither version bounds the connector number in its schema.** Severity: minor, and in the standard
rather than the code. The project assumed 2.0.1 constrained `evseId` to non-negative integers and wrote a test
for it. The test failed: both versions type the field as a plain integer with no minimum. A station therefore
cannot lean on the schema to reject connector `-1` or `9`; it has to check the number against its own hardware
and answer `PropertyConstraintViolation` itself. The test was rewritten to state the fact as it is, and
`test_an_order_for_a_connector_that_is_not_there_is_refused_with_the_right_reason` pins the station's own check.

**EVC-3 — OCPP 2.0.1 reported the same status twice in a row.** Severity: major. 2.0.1 collapses several of the
1.6 connector statuses into `Occupied`, so two events that are different for the station — a cable going in, and
charging starting — became two identical `StatusNotification` messages. A back office counting status changes
would see a change that did not happen. Fixed by suppressing a status equal to the one last reported, with a
`forced` flag so that `TriggerMessage` can still ask for the current status at any time. Pinned by
`test_every_change_of_status_is_reported_once` and by planted bug M40.

**EVC-4 — messages kept during an outage did not say they happened offline.** Severity: major. 2.0.1 marks each
transaction event that occurred while the station was unreachable, which is how a back office tells a gap in the
numbering from a loss of data. The flag was always false, because the messages were built at the moment they were
finally sent — by then the link was back. Fixed by recording the flag on the event when it happens, not when it
is sent. Pinned by `test_in_2_0_1_the_messages_kept_during_an_outage_say_so` and by planted bug M38.

**EVC-5 — after an outage, the end of a 1.6 session named the wrong transaction.** Severity: critical; this is
the one that would have produced wrong invoices. In 1.6 the back office assigns the transaction number in its
answer to `StartTransaction`. While the link was down the station queued whole messages in advance, so the
queued `StopTransaction` carried the placeholder it had at queue time instead of the number the back office
handed out on reconnection. Fixed by queueing **events** and building each message at the moment it is sent, one
at a time. Pinned by `test_the_end_of_a_session_carries_the_number_the_back_office_gave_it`,
`test_a_session_that_begins_and_ends_during_an_outage_is_billed_once`, and by planted bug M19.

Four planted bugs survived the first mutation run (M06, M09, M35, M41) — none of them a defect in the simulator,
all of them a gap in the suite: an answer matched to the wrong outstanding call, a refusal with the wrong error
code, a station that starts working although the back office rejected it, and a station let in while speaking a
version the back office does not speak. Four tests were added, and the second run caught all 41.

No defect is open.

## 4. Deviations from the plan

- **No containerised third-party back office.** The plan's interoperability section was to include a run against
  CitrineOS or EVerest in Docker. The container registry is not reachable from the environment this project was
  built in, so interoperability is proven against the reference `ocpp` library instead — a second implementation
  by other authors, but a library rather than a product. Running the suite against a packaged back office is the
  first item for a next version.
- **Two versions, not three.** OCPP 2.1 schemas are present in the `ocpp` package (181 files) and are not used.
  The step from 1.6 to 2.0.1 is the one the industry is making, and it is where the interesting differences are.
- **The WebSocket passes test the transport only.** Stated in the plan, repeated here because it explains the
  large skip counts above.

## 5. Assessment

The exit criteria of the test plan are met: four green passes and a green page suite, no untranslated text in the
page build, no difference between the twin and the Python station, 41 of 41 planted bugs caught with a passing
control run, and every finding recorded with its fix.

The suite's weight sits where the money and the safety are: what is billed, what happens when the network drops
mid-charge, and how much current a car is actually offered. The weakest point is the one named in section 4 — the
counterparty is a library rather than a deployed back office — and it is the next thing to improve.
