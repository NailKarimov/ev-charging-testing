# Mutation check

**41/41 planted bugs caught.** Control runs without a planted bug: passed.

| Id | File | Planted bug | OCPP | Result | Tests that failed |
| --- | --- | --- | --- | --- | --- |
| M01 | `src/evse_lab/rpc.py` | A message with extra parts bolted on is accepted | 1.6 | caught | 1 |
| M02 | `src/evse_lab/rpc.py` | A message id of any length is accepted | 1.6 | caught | 1 |
| M03 | `src/evse_lab/rpc.py` | Anything at all is accepted where the payload should be | 1.6 | caught | 3 |
| M04 | `src/evse_lab/rpc.py` | Any number at the front is treated as a call | 1.6 | caught | 2 |
| M05 | `src/evse_lab/node.py` | The station tries to carry out orders it does not support instead of refusing them | 1.6 | caught | 3 |
| M06 | `src/evse_lab/node.py` | An answer is matched to whatever message is waiting, whoever it was meant for | 1.6 | caught | 1 |
| M07 | `src/evse_lab/node.py` | The station sends its next message without waiting for the answer to the last one | 1.6 | caught | 15 |
| M08 | `src/evse_lab/node.py` | Orders are carried out without checking them against the official schema | 1.6 | caught | 4 |
| M09 | `src/evse_lab/node.py` | A refusal gives the wrong reason, so the other side cannot tell what went wrong | 1.6 | caught | 1 |
| M10 | `src/evse_lab/station.py` | Any card starts a session, including one the back office blocked | 1.6 | caught | 6 |
| M11 | `src/evse_lab/station.py` | Anybody's card can stop somebody else's charging session | 1.6 | caught | 1 |
| M12 | `src/evse_lab/station.py` | While the link is down every card is let through, even one never seen before | 1.6 | caught | 2 |
| M13 | `src/evse_lab/station.py` | Every session is billed as zero energy | 1.6 | caught | 5 |
| M14 | `src/evse_lab/station.py` | A session starts from zero instead of from the meter reading, so the bill is the whole lifetime | 1.6 | caught | 4 |
| M15 | `src/evse_lab/station.py` | The meter counts the current offered, not the current the car took | 1.6 | caught | 5 |
| M16 | `src/evse_lab/csms.py` | The back office gives every session the same number, so they overwrite each other | 1.6 | caught | 2 |
| M17 | `src/evse_lab/station.py` | Everything that happens while the link is down is thrown away | 1.6 | caught | 4 |
| M18 | `src/evse_lab/station.py` | What was kept during an outage is handed over backwards | 1.6 | caught | 3 |
| M19 | `src/evse_lab/node.py` | Messages kept during an outage are written in advance, so the end of a session names the wrong transaction | 1.6 | caught | 3 |
| M20 | `src/evse_lab/station.py` | The station talks into a dead link and loses what it had to say | 1.6 | caught | 3 |
| M21 | `src/evse_lab/station.py` | Pulling the cable out leaves the session running for ever | 1.6 | caught | 1 |
| M22 | `src/evse_lab/station.py` | A car that is not taking power is still reported as charging | 1.6 | caught | 3 |
| M23 | `src/evse_lab/station.py` | A station giving nothing still reports the car as charging | 1.6 | caught | 2 |
| M24 | `src/evse_lab/station.py` | A fault on the pilot wire is never reported | 1.6 | caught | 1 |
| M25 | `src/evse_lab/station.py` | The cable can be released while a car is charging | 1.6 | caught | 1 |
| M26 | `src/evse_lab/station.py` | A meter reading is sent every second instead of every minute | 1.6 | caught | 7 |
| M27 | `src/evse_lab/station.py` | The station calls in every second instead of at the interval it was given | 1.6 | caught | 9 |
| M28 | `src/evse_lab/station.py` | With two limits in force the station takes the larger one and gives more than allowed | 1.6 | caught | 1 |
| M29 | `src/evse_lab/station.py` | The stack level is read upside down, so the wrong limit wins | 1.6 | caught | 1 |
| M30 | `src/evse_lab/station.py` | A limit that has run out stays in force for ever | 1.6 | caught | 1 |
| M31 | `src/evse_lab/station.py` | A limit meant for later bites straight away | 1.6 | caught | 1 |
| M32 | `src/evse_lab/station.py` | The station offers a car less than 6 A, which no car may take | 1.6 | caught | 2 |
| M33 | `src/evse_lab/station.py` | The car takes what it wants and the operator's limit is ignored | 1.6 | caught | 13 |
| M34 | `src/evse_lab/v16.py` | The end of a session names the station's own number instead of the back office's | 1.6 | caught | 10 |
| M35 | `src/evse_lab/v16.py` | The station starts working even when the back office did not accept it | 1.6 | caught | 2 |
| M36 | `src/evse_lab/v201.py` | Every event of a session carries the number zero, so a gap cannot be seen | 2.0.1 | caught | 2 |
| M37 | `src/evse_lab/v201.py` | The transaction id changes between messages, so the session is split in two | 2.0.1 | caught | 2 |
| M38 | `src/evse_lab/v201.py` | Messages kept during an outage do not say so | 2.0.1 | caught | 1 |
| M39 | `src/evse_lab/dialect.py` | A meter reading is written in the shape of the wrong version | 2.0.1 | caught | 59 |
| M40 | `src/evse_lab/v201.py` | The same status is reported over and over when nothing has changed | 2.0.1 | caught | 2 |
| M41 | `src/evse_lab/link.py` | A station speaking another version of OCPP is let in anyway | 1.6 | caught | 1 |
