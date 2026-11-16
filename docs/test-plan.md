# Test plan — ev-charging-testing

Structure after ISO/IEC/IEEE 29119-3. One page, because the project is one component.

## 1. Context

| Item               | Value                                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------------------------- |
| Item under test    | `evse_lab` 1.0.0 — a simulated two-connector AC charge point that speaks OCPP 1.6J and OCPP 2.0.1      |
| Purpose            | Show, on a portfolio project, how a device protocol is tested against a published standard rather than against itself |
| Audience           | Interviewers and reviewers, including those who do not read code; hence the visual page beside the report |
| Test levels        | Component and component-integration. No system or acceptance level: there is no product around the component |
| Standards involved | OCPP 1.6 Edition 2 (JSON over WebSocket), OCPP 2.0.1 Edition 3, IEC 61851-1 for the control-pilot states |

## 2. What is in scope

1. **The envelope.** OCPP-J: a call `[2, id, action, payload]`, an answer `[3, id, payload]`, a refusal
   `[4, id, errorCode, description, details]`. Which damaged message gets which of the standard's error codes,
   and when a message cannot be answered at all.
2. **Message validity.** Every message sent and received is validated against the JSON schema published with the
   standard. Required fields, unknown fields, values outside an enumeration, wrong types.
3. **A charging session.** Cable in, card presented, authorisation, start, meter values, stop, cable out, and
   what the back office is told at each step. The energy billed equals the energy the meter counted.
4. **Authorisation.** Accepted, blocked, expired and unknown cards; the authorisation cache while the link is
   down; one driver's card cannot stop another driver's session.
5. **Loss of network.** Charging continues, nothing is sent into a dead link, everything owed is delivered in
   order when the link returns, and a session that began and ended offline is billed exactly once.
6. **Smart charging.** A limit in amps, two limits at once, stack levels, a validity window that opens later and
   closes, and the 6 A floor of IEC 61851-1 below which a station must pause rather than trickle.
7. **Orders from the back office.** Remote start and stop, soft and hard reset, change of the call-in interval,
   taking a connector out of service and back, releasing the cable, triggering a message.
8. **Connector status.** The pilot states A/B/C/E and the one word each version reports for them, including the
   statuses 2.0.1 collapses into `Occupied`.
9. **Robustness.** Random text, oversized payloads, deeply nested payloads, answers that match no question, and
   an order that arrives while the station is itself waiting for an answer.
10. **Interoperability.** The reference `ocpp` library as the counterparty, in both directions, over a real
    WebSocket.
11. **The visual page.** The five views open, switch language, show the real run, and react to a planted bug.

## 3. What is out of scope

- Real hardware. There is no charge point and no car; the pilot wire, the meter and the card reader are modelled.
- OCPP security profiles, certificates and firmware update; ISO 15118 plug-and-charge; OCPP 2.1.
- Load balancing across several charge points, tariffs, reservations and display messages.
- Performance and endurance. The suite proves behaviour, not throughput.

## 4. Approach

| Technique                      | Where                                                       | Why                                                                                         |
| -------------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Specification-based              | every suite                                                 | The standard says what each message must contain and when it is sent                        |
| Schema validation as an oracle | `test_schemas`, and both directions of every other suite    | An independent judge: the schema files ship with the standard, not with this repository     |
| Interoperability testing       | `test_interop`                                              | A second implementation, written by other people, has to understand the same traffic        |
| Equivalence and boundary       | `test_smart_charging`, `test_remote_commands`               | 6 A exactly, a connector that does not exist, a window that has just closed                 |
| Negative testing               | `test_envelope`, `test_schemas`, `test_robustness`          | A refusal has to name the right reason, and nothing may act on a message it refused         |
| Property-based (Hypothesis)    | `test_envelope`, `test_robustness`                          | Whatever text arrives, it is either a valid message or refused — never half-understood      |
| State-transition               | `test_session`, `test_connector_status`, `test_offline`     | The pilot states and the connector statuses form a machine with the session running over it |
| Mutation testing               | `scripts/mutants.py`                                        | 41 planted bugs measure whether the suite would notice a real one                           |
| Cross-implementation twin      | `site/test/twin.test.mjs`                                   | The JavaScript model in the page has to answer like the Python one, or the page lies        |

Each test carries exactly one assertion-worthy claim, written as the docstring sentence. Collection fails without
it, so the report and the visual page cannot drift from the code.

## 5. Test environment

| Item         | Value                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------ |
| Runtime      | Python 3.12 on Linux; Node.js 24 for the twin; Chromium through Playwright for the page                    |
| Dependencies | pytest, pytest-asyncio, pytest-timeout, Hypothesis, websockets, jsonschema, `ocpp`, allure-pytest, ruff, mypy |
| Links        | `LINK=memory` — the two endpoints in one process; `LINK=websocket` — a real server and client on localhost |
| Versions     | `OCPP=1.6` (default) and `OCPP=2.0.1`                                                                      |
| Clock        | Simulated. Tests advance time explicitly, so every run sees the same session                               |
| CI           | GitHub Actions: lint, type check, four passes of the suite, page build, twin check, browser tests          |

The matrix is four passes. Behaviour runs in memory on both versions; the WebSocket passes test the transport
only, because the station behaves the same whichever way the bytes travel, and repeating 148 behaviour tests over
a socket would buy nothing but minutes.

## 6. Entry and exit criteria

**Entry.** The package installs, `ruff` and `mypy` are clean, and the `ocpp` package ships the schema files the
project reads.

**Exit.** All four passes green and the 21 browser tests green; `build_pages.py --check` leaves no text
untranslated; the twin check reports no difference; the mutation check catches 41 of 41 with the control run
passing; every finding recorded in the test report with its fix.

## 7. Risks

| Risk                                                             | Handling                                                                              |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| The simulator and its tests share one author's misunderstanding | The schemas and the reference library are independent of both                         |
| A test passes because it asserts what the code happens to do    | Mutation testing: a bug planted in that code must turn some test red                   |
| The visual page shows something other than the real run         | The page is built from the evidence files the tests write, and the twin is checked      |
| Timing flakiness over a real socket                             | Simulated clock for behaviour; the socket passes settle explicitly, with a timeout      |
| The two versions drift apart in the model                       | One station, two dialects; a behaviour test runs unchanged on both                      |

## 8. Deliverables

Allure report, the five-view visual page, `docs/test-report.md`, `mutants-result.md`, and the evidence files
`evidence/run-<version>-<link>.json`.
