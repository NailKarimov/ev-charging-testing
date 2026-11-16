"""Mutation check: plant one bug at a time and make sure the test suite notices.

    python scripts/mutants.py              # every mutant
    python scripts/mutants.py M07 M21      # only these

Each mutant is one small change to the source, applied to a private copy of the project. The suite then
runs against the copy on the OCPP version the mutant belongs to. A mutant is "caught" if at least one
test fails. A control run with no change must pass, otherwise the result means nothing.

Writes ``evidence/mutants.json`` (shown on the "Bug hunt" page) and ``mutants-result.md``.
Exit code 1 if a mutant survives or a control run fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "src/evse_lab/"
V16, V201 = "1.6", "2.0.1"


@dataclass(frozen=True)
class Mutant:
    id: str
    area: str  # envelope | session | money | safety | smart | dialect
    file: str
    old: str
    new: str
    title: str  # what is broken, in plain words
    version: str = V16
    link: str = "memory"  # a few mutants only show on a real connection


MUTANTS = [
    # ---- the envelope: the rules of talking ----------------------------------------------------
    Mutant(
        "M01",
        "envelope",
        "rpc.py",
        "if len(body) != 4:",
        "if len(body) < 4:",
        "A message with extra parts bolted on is accepted",
    ),
    Mutant(
        "M02",
        "envelope",
        "rpc.py",
        "if len(message_id) > MAX_ID_LENGTH:",
        "if False:",
        "A message id of any length is accepted",
    ),
    Mutant(
        "M03",
        "envelope",
        "rpc.py",
        "if not isinstance(payload, dict):",
        "if False:",
        "Anything at all is accepted where the payload should be",
    ),
    Mutant(
        "M04",
        "envelope",
        "rpc.py",
        'raise MalformedMessage(f"unknown message type {kind!r}", PROTOCOL_ERROR)',
        "kind = MessageType.CALL",
        "Any number at the front is treated as a call",
    ),
    Mutant(
        "M05",
        "envelope",
        "node.py",
        "        if call.action not in self.supported():",
        "        if False:",
        "The station tries to carry out orders it does not support instead of refusing them",
    ),
    Mutant(
        "M06",
        "envelope",
        "node.py",
        "        if outstanding is None or message.id != outstanding.call.id:",
        "        if outstanding is None:",
        "An answer is matched to whatever message is waiting, whoever it was meant for",
    ),
    Mutant(
        "M07",
        "envelope",
        "node.py",
        "        if self.outstanding is not None or not self.pending:",
        "        if not self.pending:",
        "The station sends its next message without waiting for the answer to the last one",
    ),
    Mutant(
        "M08",
        "envelope",
        "node.py",
        "            schemas.validate(self.version, call.action, call.payload)\n        except schemas.SchemaViolation as bad:",
        "            pass\n        except schemas.SchemaViolation as bad:",
        "Orders are carried out without checking them against the official schema",
    ),
    Mutant(
        "M09",
        "envelope",
        "node.py",
        "            self._refuse(call.id, rpc.PROPERTY_CONSTRAINT_VIOLATION, str(bad))",
        "            self._refuse(call.id, rpc.INTERNAL_ERROR, str(bad))",
        "A refusal gives the wrong reason, so the other side cannot tell what went wrong",
    ),
    # ---- who may charge, and what is billed --------------------------------------------------------
    Mutant(
        "M10",
        "money",
        "station.py",
        "        if status is AuthStatus.ACCEPTED:\n            self.start_transaction(id_tag, connector)",
        "        self.start_transaction(id_tag, connector)",
        "Any card starts a session, including one the back office blocked",
    ),
    Mutant(
        "M11",
        "money",
        "station.py",
        "            if c.transaction.id_tag == id_tag:\n                self.stop_transaction(connector, Reason.LOCAL)\n                return AuthStatus.ACCEPTED\n            return AuthStatus.CONCURRENT_TX",
        "            self.stop_transaction(connector, Reason.LOCAL)\n            return AuthStatus.ACCEPTED",
        "Anybody's card can stop somebody else's charging session",
    ),
    Mutant(
        "M12",
        "money",
        "station.py",
        "        return self.auth_cache.get(id_tag, AuthStatus.INVALID)",
        "        return AuthStatus.ACCEPTED",
        "While the link is down every card is let through, even one never seen before",
    ),
    Mutant(
        "M13",
        "money",
        "station.py",
        "        transaction.meter_stop = int(self.meter_wh)",
        "        transaction.meter_stop = transaction.meter_start",
        "Every session is billed as zero energy",
    ),
    Mutant(
        "M14",
        "money",
        "station.py",
        "            meter_start=int(self.meter_wh),",
        "            meter_start=0,",
        "A session starts from zero instead of from the meter reading, so the bill is the whole lifetime",
    ),
    Mutant(
        "M15",
        "money",
        "station.py",
        "            if drawn > 0:\n                self.meter_wh += NOMINAL_V * drawn / 3600.0",
        "            self.meter_wh += NOMINAL_V * c.offered_a(self.clock) / 3600.0",
        "The meter counts the current offered, not the current the car took",
    ),
    Mutant(
        "M16",
        "money",
        "csms.py",
        "        number = next(self._numbers)",
        "        number = 1001",
        "The back office gives every session the same number, so they overwrite each other",
    ),
    # ---- the link going down -------------------------------------------------------------------
    Mutant(
        "M17",
        "safety",
        "station.py",
        "        else:\n            self.queue.append(replace(event, offline=True))",
        "        else:\n            pass",
        "Everything that happens while the link is down is thrown away",
    ),
    Mutant(
        "M18",
        "safety",
        "station.py",
        "        self.events.extend(self.queue)",
        "        self.events.extend(reversed(self.queue))",
        "What was kept during an outage is handed over backwards",
    ),
    Mutant(
        "M19",
        "safety",
        "node.py",
        "        while not self.pending and self.waiting:\n            # An event may become no message at all: see the duplicate statuses of OCPP 2.0.1.\n            self.pending.extend(self.dialect.calls_for(self.station, self.waiting.pop(0)))",
        "        while self.waiting:\n            self.pending.extend(self.dialect.calls_for(self.station, self.waiting.pop(0)))",
        "Messages kept during an outage are written in advance, so the end of a session names the wrong transaction",
        version=V16,
    ),
    Mutant(
        "M20",
        "safety",
        "station.py",
        "        if self.online:\n            self.events.append(event)",
        "        if True:\n            self.events.append(event)",
        "The station talks into a dead link and loses what it had to say",
    ),
    # ---- the car, the cable and the pilot wire ------------------------------------------------------
    Mutant(
        "M21",
        "session",
        "station.py",
        "        if c.transaction is not None and c.transaction.running:\n            self.stop_transaction(connector, Reason.EV_DISCONNECTED)",
        "        if False:\n            self.stop_transaction(connector, Reason.EV_DISCONNECTED)",
        "Pulling the cable out leaves the session running for ever",
    ),
    Mutant(
        "M22",
        "session",
        "station.py",
        "            if c.pilot is not Pilot.C:\n                return Status.SUSPENDED_EV",
        "            if False:\n                return Status.SUSPENDED_EV",
        "A car that is not taking power is still reported as charging",
    ),
    Mutant(
        "M23",
        "session",
        "station.py",
        "            if c.offered_a(self.clock) <= 0:\n                return Status.SUSPENDED_EVSE",
        "            if False:\n                return Status.SUSPENDED_EVSE",
        "A station giving nothing still reports the car as charging",
    ),
    Mutant(
        "M24",
        "session",
        "station.py",
        "        if c.pilot is Pilot.E:\n            return Status.FAULTED",
        "        if False:\n            return Status.FAULTED",
        "A fault on the pilot wire is never reported",
    ),
    Mutant(
        "M25",
        "session",
        "station.py",
        '        if c.transaction is not None and c.transaction.running:\n            return "UnlockFailed"',
        '        if False:\n            return "UnlockFailed"',
        "The cable can be released while a car is charging",
    ),
    Mutant(
        "M26",
        "session",
        "station.py",
        "                due = self.last_sample.get(number, self.clock) + METER_SAMPLE_S",
        "                due = self.last_sample.get(number, self.clock)",
        "A meter reading is sent every second instead of every minute",
    ),
    Mutant(
        "M27",
        "session",
        "station.py",
        "        if self.booted and self.clock - self.last_heartbeat >= self.boot_interval:",
        "        if self.booted and self.clock - self.last_heartbeat >= 1:",
        "The station calls in every second instead of at the interval it was given",
    ),
    # ---- smart charging -----------------------------------------------------------------------------
    Mutant(
        "M28",
        "smart",
        "station.py",
        "            allowed = min(allowed, min(p.limit_a for p in active if p.stack_level == top))",
        "            allowed = min(allowed, max(p.limit_a for p in active if p.stack_level == top))",
        "With two limits in force the station takes the larger one and gives more than allowed",
    ),
    Mutant(
        "M29",
        "smart",
        "station.py",
        "            top = max(p.stack_level for p in active)",
        "            top = min(p.stack_level for p in active)",
        "The stack level is read upside down, so the wrong limit wins",
    ),
    Mutant(
        "M30",
        "smart",
        "station.py",
        "        return not (self.valid_to is not None and now >= self.valid_to)",
        "        return True",
        "A limit that has run out stays in force for ever",
    ),
    Mutant(
        "M31",
        "smart",
        "station.py",
        "        if self.valid_from is not None and now < self.valid_from:\n            return False",
        "        if False:\n            return False",
        "A limit meant for later bites straight away",
    ),
    Mutant(
        "M32",
        "smart",
        "station.py",
        "        return 0.0 if limit < MIN_CHARGE_A else limit",
        "        return limit",
        "The station offers a car less than 6 A, which no car may take",
    ),
    Mutant(
        "M33",
        "smart",
        "station.py",
        "        return min(self.offered_a(now), self.car_max_a)",
        "        return self.car_max_a",
        "The car takes what it wants and the operator's limit is ignored",
    ),
    # ---- the two versions ----------------------------------------------------------------------------
    Mutant(
        "M34",
        "dialect",
        "v16.py",
        '"transactionId": transaction.remote_id if transaction.remote_id is not None else -transaction.local_id,',
        '"transactionId": transaction.local_id,',
        "The end of a session names the station's own number instead of the back office's",
        version=V16,
    ),
    Mutant(
        "M35",
        "dialect",
        "v16.py",
        '            if payload.get("status") == "Accepted":\n                station.accept_boot(int(payload.get("interval", 300)))',
        '            station.accept_boot(int(payload.get("interval", 300)))',
        "The station starts working even when the back office did not accept it",
        version=V16,
    ),
    Mutant(
        "M36",
        "dialect",
        "v201.py",
        "        transaction.seq_no += 1",
        "        pass",
        "Every event of a session carries the number zero, so a gap cannot be seen",
        version=V201,
    ),
    Mutant(
        "M37",
        "dialect",
        "v201.py",
        '    return f"TX-{local_id:06d}"',
        '    return f"TX-{local_id:06d}-{id(local_id) % 1000}"',
        "The transaction id changes between messages, so the session is split in two",
        version=V201,
    ),
    Mutant(
        "M38",
        "dialect",
        "v201.py",
        '        if event.offline:\n            payload["offline"] = True',
        '        if False:\n            payload["offline"] = True',
        "Messages kept during an outage do not say so",
        version=V201,
    ),
    Mutant(
        "M39",
        "dialect",
        "dialect.py",
        '        if version == "1.6":',
        "        if True:",
        "A meter reading is written in the shape of the wrong version",
        version=V201,
    ),
    Mutant(
        "M40",
        "dialect",
        "v201.py",
        "            if not event.forced and self._reported.get(event.connector) == name:\n                return []",
        "            if False:\n                return []",
        "The same status is reported over and over when nothing has changed",
        version=V201,
    ),
    Mutant(
        "M41",
        "dialect",
        "link.py",
        "        if SUBPROTOCOLS[self.version] in asked:\n            return None",
        "        if True:\n            return None",
        "A station speaking another version of OCPP is let in anyway",
        version=V16,
        link="websocket",  # the handshake only happens on a real connection
    ),
]

IGNORE = shutil.ignore_patterns(
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "evidence",
    "allure-*",
    "site",
    "docs",
)


def run_suite(workdir: Path, version: str, link: str) -> tuple[bool, list[dict[str, str]], float]:
    """Run the suite in ``workdir``. Returns (passed, failed tests, seconds)."""
    env = {
        **os.environ,
        "OCPP": version,
        "LINK": link,
        "PYTHONDONTWRITEBYTECODE": "1",
        "EVIDENCE": "1",
    }
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "not pages"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
    )
    seconds = time.monotonic() - started
    failed: list[dict[str, str]] = []
    evidence = workdir / "evidence" / f"run-{version}-{link}.json"
    if evidence.exists():
        for test in json.loads(evidence.read_text())["tests"]:
            if test["outcome"] == "failed":
                failed.append({"id": test["id"], "name": test["name"], "title": test["title"], "suite": test["suite"]})
    if result.returncode not in (0, 1):
        failed.append({"id": "pytest", "name": "pytest", "title": result.stdout[-400:], "suite": "pytest"})
    return result.returncode == 0, failed, seconds


def check(mutant: Mutant | None, tmp: Path, version: str, link: str = "memory") -> dict[str, object]:
    name = mutant.id if mutant else f"control-{version}-{link}"
    work = tmp / name
    shutil.copytree(ROOT, work, ignore=IGNORE)
    if mutant:
        target = work / SRC / mutant.file
        text = target.read_text()
        if text.count(mutant.old) != 1:
            raise SystemExit(f"{mutant.id}: pattern found {text.count(mutant.old)} times in {mutant.file}, expected 1")
        target.write_text(text.replace(mutant.old, mutant.new))
    passed, failed, seconds = run_suite(work, version, link)
    shutil.rmtree(work, ignore_errors=True)
    return {"passed": passed, "failed": failed, "seconds": round(seconds, 1)}


def main(argv: list[str]) -> int:
    chosen = [m for m in MUTANTS if not argv or m.id in argv]
    workers = int(os.getenv("MUTANT_WORKERS", str(min(6, os.cpu_count() or 2))))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mutants-") as tmp_name, ThreadPoolExecutor(workers) as pool:
        tmp = Path(tmp_name)
        pairs = sorted({(m.version, m.link) for m in chosen})
        controls = {p: pool.submit(check, None, tmp, p[0], p[1]) for p in pairs}
        futures = {m.id: pool.submit(check, m, tmp, m.version, m.link) for m in chosen}
        control_ok = True
        for (version, link), future in controls.items():
            outcome = future.result()
            print(
                f"control (OCPP {version}, {link}): {'passed' if outcome['passed'] else 'FAILED'} in {outcome['seconds']} s"
            )
            if not outcome["passed"]:
                control_ok = False
                print(json.dumps(outcome["failed"], indent=1)[:2000], file=sys.stderr)
        results = []
        for mutant in chosen:
            outcome = futures[mutant.id].result()
            failed = outcome["failed"]
            assert isinstance(failed, list)
            caught = not outcome["passed"]
            print(
                f"{mutant.id}: {'caught by ' + str(len(failed)) + ' tests' if caught else 'SURVIVED'} - {mutant.title}"
            )
            results.append(
                {
                    "id": mutant.id,
                    "area": mutant.area,
                    "file": SRC + mutant.file,
                    "title": mutant.title,
                    "version": mutant.version,
                    "link": mutant.link,
                    "old": mutant.old.strip("\n"),
                    "new": mutant.new.strip("\n"),
                    "caught": caught,
                    "killers": failed[:12],
                    "killer_count": len(failed),
                    "seconds": outcome["seconds"],
                }
            )

    survived = [r for r in results if not r["caught"]]
    total = len(results)
    meta = {
        "finished": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": os.getenv("GITHUB_SHA", "")[:7],
        "total": total,
        "caught": total - len(survived),
        "control": control_ok,
        "seconds": round(time.monotonic() - started),
    }
    out = ROOT / "evidence"
    out.mkdir(exist_ok=True)
    (out / "mutants.json").write_text(
        json.dumps({"meta": meta, "mutants": results}, ensure_ascii=False, indent=1) + "\n"
    )

    lines = [
        "# Mutation check",
        "",
        f"**{meta['caught']}/{total} planted bugs caught.** Control runs without a planted bug: "
        f"{'passed' if control_ok else 'FAILED'}.",
        "",
        "| Id | File | Planted bug | OCPP | Result | Tests that failed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        verdict = "caught" if r["caught"] else "**survived**"
        lines.append(f"| {r['id']} | `{r['file']}` | {r['title']} | {r['version']} | {verdict} | {r['killer_count']} |")
    (ROOT / "mutants-result.md").write_text("\n".join(lines) + "\n")
    print(f"{meta['caught']}/{total} caught in {meta['seconds']} s")
    return 1 if survived or not control_ok else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
