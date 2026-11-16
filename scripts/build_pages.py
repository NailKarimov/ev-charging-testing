"""Build the visual page ``docs/ev-charging.html`` from ``site/`` and the evidence of the last run.

    python scripts/build_pages.py            # data + page
    python scripts/build_pages.py --check    # also fail if a test or a mutant has no Russian title

What it produces:

- ``site/data/spec.js``      what the two versions of OCPP look like, exported from the source
- ``site/data/run.js``       the last test run on every version and link, slimmed down for the page
- ``site/data/mutants.js``   the result of the mutation check
- ``site/test/vectors.json`` conversations recorded from the Python station; ``site/test/twin.test.mjs``
                             replays them against the JavaScript twin and compares every message
- ``docs/ev-charging.html``  one self-contained file: styles, scripts and data inlined
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from evse_lab import __version__, schemas  # noqa: E402
from evse_lab.csms import Csms  # noqa: E402
from evse_lab.link import memory_pair  # noqa: E402
from evse_lab.node import Conversation, CsmsEndpoint, StationEndpoint  # noqa: E402
from evse_lab.schemas import V16, V201  # noqa: E402
from evse_lab.station import (  # noqa: E402
    EVSE_MAX_A,
    MIN_CHARGE_A,
    NOMINAL_V,
    ChargePoint,
    Pilot,
    Status,
)
from evse_lab.v16 import SUPPORTED as V16_SUPPORTED  # noqa: E402
from evse_lab.v16 import V16Dialect  # noqa: E402
from evse_lab.v201 import SUPPORTED as V201_SUPPORTED  # noqa: E402
from evse_lab.v201 import V201Dialect, _status_name  # noqa: E402

SITE = ROOT / "site"
DATA = SITE / "data"
EVIDENCE = ROOT / "evidence"
OUT = ROOT / "docs" / "ev-charging.html"
RUNS = ((V16, "memory"), (V201, "memory"), (V16, "websocket"), (V201, "websocket"))
PAGE_FRAMES = 20  # messages kept per test on the page


def js(name: str, value: Any) -> str:
    return f"window.{name} = {json.dumps(value, ensure_ascii=False, separators=(',', ':'))};\n"


# ---- what the two versions look like ------------------------------------------------------------


def export_spec() -> dict[str, Any]:
    """Everything the page needs to explain OCPP, taken from the source rather than retyped."""
    # fmt: off  (one version per line reads better than the formatter's split)
    station_messages = {
        V16: [
            "BootNotification",
            "StatusNotification",
            "Authorize",
            "StartTransaction",
            "MeterValues",
            "StopTransaction",
            "Heartbeat",
        ],
        V201: ["BootNotification", "StatusNotification", "Authorize", "TransactionEvent", "Heartbeat"],
    }
    # fmt: on
    return {
        "release": __version__,
        "versions": {
            V16: {
                "name": "OCPP 1.6J",
                "subprotocol": "ocpp1.6",
                "messages": sorted(schemas.actions(V16)),
                "fromStation": station_messages[V16],
                "orders": sorted(V16_SUPPORTED),
                "schemaCount": len(schemas.actions(V16)),
                "draft": "draft-04",
            },
            V201: {
                "name": "OCPP 2.0.1",
                "subprotocol": "ocpp2.0.1",
                "messages": sorted(schemas.actions(V201)),
                "fromStation": station_messages[V201],
                "orders": sorted(V201_SUPPORTED),
                "schemaCount": len(schemas.actions(V201)),
                "draft": "draft-06",
            },
        },
        "statuses": [{"name": s.value, "v201": _status_name(s)} for s in Status],
        "pilot": [p.value for p in Pilot],
        "limits": {"evseMaxA": EVSE_MAX_A, "minChargeA": MIN_CHARGE_A, "volts": NOMINAL_V},
        "cards": ["CARD-OK", "CARD-OK-2", "CARD-BLOCKED", "CARD-EXPIRED"],
    }


# ---- conversations for the twin -------------------------------------------------------------------

# A scenario is a list of steps the JavaScript twin can replay exactly. One conversation per line.
# fmt: off
SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "a whole session, half an hour",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 1800, 300], ["card", "CARD-OK", 1], ["unplug", 1], ["run", 5, 5]],
    },
    {
        "name": "the link drops in the middle",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 300, 60], ["offline"], ["run", 900, 60], ["online"], ["run", 120, 60], ["card", "CARD-OK", 1], ["unplug", 1], ["run", 5, 5]],
    },
    {
        "name": "a whole session with nobody listening",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["offline"], ["ready", 1, True], ["run", 1200, 120], ["card", "CARD-OK", 1], ["unplug", 1], ["run", 5, 5], ["online"], ["run", 5, 5]],
    },
    {
        "name": "cards that are refused",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-BLOCKED", 1], ["card", "CARD-EXPIRED", 1], ["card", "CARD-NOBODY", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 120, 60], ["card", "CARD-OK-2", 1], ["run", 60, 60]],
    },
    {
        "name": "the operator limits the power",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 120, 60], ["profile", 1, 10.0, 1, 0], ["run", 180, 60], ["profile", 1, 4.0, 2, 1], ["run", 180, 60], ["clear", 1, 2], ["run", 180, 60], ["clear", 1, 1], ["run", 120, 60]],
    },
    {
        "name": "the car pauses and comes back",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 180, 60], ["ready", 1, False], ["run", 300, 60], ["ready", 1, True], ["run", 300, 60], ["card", "CARD-OK", 1], ["run", 5, 5]],
    },
    {
        "name": "two connectors at once",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 300, 120], ["plug", 2], ["card", "CARD-OK-2", 2], ["ready", 2, True], ["run", 600, 120], ["card", "CARD-OK", 1], ["unplug", 1], ["run", 300, 120], ["card", "CARD-OK-2", 2], ["run", 5, 5]],
    },
    {
        "name": "the cable is pulled out mid-charge",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 420, 60], ["unplug", 1], ["run", 10, 5]],
    },
    {
        "name": "orders from the back office",
        "steps": [["boot"], ["plug", 1], ["order", "unlock", 1], ["order", "operative", 1, False], ["order", "operative", 1, True], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 120, 60], ["order", "unlock", 1], ["card", "CARD-OK", 1], ["run", 5, 5], ["order", "unlock", 1]],
    },
    {
        "name": "a fault on the pilot wire",
        "steps": [["boot"], ["plug", 1], ["card", "CARD-OK", 1], ["ready", 1, True], ["run", 180, 60], ["fault", 1], ["run", 120, 60]],
    },
]
# fmt: on


def _random_scenario() -> dict[str, Any]:
    rng = random.Random(20261116)
    steps: list[list[Any]] = [["boot"]]
    for _ in range(220):
        roll = rng.random()
        connector = rng.choice([1, 2])
        if roll < 0.22:
            steps.append(["run", rng.choice([1, 30, 60, 120, 300, 900]), 60])
        elif roll < 0.34:
            steps.append(["plug", connector])
        elif roll < 0.46:
            steps.append(["card", rng.choice(["CARD-OK", "CARD-OK-2", "CARD-BLOCKED", "CARD-NOBODY"]), connector])
        elif roll < 0.58:
            steps.append(["ready", connector, rng.random() < 0.6])
        elif roll < 0.66:
            steps.append(["unplug", connector])
        elif roll < 0.74:
            steps.append(
                [
                    "profile",
                    connector,
                    rng.choice([4.0, 6.0, 8.0, 10.0, 16.0, 32.0]),
                    rng.choice([1, 2]),
                    rng.choice([0, 1]),
                ]
            )
        elif roll < 0.80:
            steps.append(["clear", connector, rng.choice([1, 2])])
        elif roll < 0.86:
            steps.append(["offline"])
        elif roll < 0.92:
            steps.append(["online"])
        else:
            steps.append(["order", rng.choice(["unlock", "operative"]), connector, rng.random() < 0.5])
    return {"name": "three hundred random actions", "steps": steps}


class Scenario:
    """Runs a scenario against the Python station and keeps every frame."""

    def __init__(self, version: str) -> None:
        self.station = ChargePoint(connectors=2)
        self.csms = Csms(version=version)
        cp_link, back_link = memory_pair()
        dialect = V16Dialect() if version == V16 else V201Dialect()
        self.cp = StationEndpoint(self.station, dialect, cp_link)
        self.talk = Conversation(self.cp, CsmsEndpoint(self.csms, back_link))
        self.version = version

    def play(self, steps: list[list[Any]]) -> list[list[str]]:
        for step in steps:
            self._do(step)
        return [[direction, text] for direction, text in self.cp.record.frames]

    def _do(self, step: list[Any]) -> None:
        name = step[0]
        if name == "boot":
            self.station.boot()
        elif name == "plug":
            self.station.plug_in(step[1])
        elif name == "unplug":
            self.station.unplug(step[1])
        elif name == "ready":
            self.station.car_ready(step[1], step[2])
        elif name == "card":
            self.station.present_card(step[1], step[2])
        elif name == "fault":
            self.station.connector(step[1]).pilot = Pilot.E
            self.station.advance(1)
        elif name == "offline":
            self.station.go_offline()
        elif name == "online":
            self.station.go_online()
        elif name == "profile":
            from evse_lab.station import Profile

            self.station.set_profile(step[1], Profile(id=step[3], limit_a=step[2], stack_level=step[4]))
        elif name == "clear":
            self.station.clear_profiles(connector=step[1], profile_id=step[2])
        elif name == "order":
            self._order(step)
        elif name == "run":
            self.talk.run(step[1], step[2])
            return
        else:
            raise AssertionError(f"unknown step {name!r}")
        self.talk.settle()

    def _order(self, step: list[Any]) -> None:
        kind = step[1]
        if kind == "unlock":
            self.station.unlock(step[2])
        elif kind == "operative":
            self.station.set_operative(step[2], bool(step[3]))


def record_vectors() -> list[dict[str, Any]]:
    out = []
    scenarios = [*SCENARIOS, _random_scenario()]
    for version in (V16, V201):
        for scenario in scenarios:
            frames = Scenario(version).play(scenario["steps"])
            out.append({"version": version, "name": scenario["name"], "steps": scenario["steps"], "frames": frames})
    return out


# ---- the last run ------------------------------------------------------------------------------


def load_run() -> dict[str, Any]:
    """Merge the runs into one record per test, with an outcome per run."""
    metas: dict[str, Any] = {}
    tests: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for version, link in RUNS:
        key = f"{version}-{link}"
        path = EVIDENCE / f"run-{key}.json"
        if not path.exists():
            continue
        run = json.loads(path.read_text())
        metas[key] = run["meta"]
        for test in run["tests"]:
            node = test["id"]
            if node not in tests:
                order.append(node)
                tests[node] = {
                    "suite": test["suite"],
                    "name": test["name"],
                    "params": test["params"],
                    "title": test["title"],
                    "runs": {},
                }
            entry = tests[node]
            entry["runs"][key] = {"o": test["outcome"][0], "ms": test["ms"], "n": test["frames"]}
            if test["outcome"] == "failed":
                entry["runs"][key]["why"] = test["reason"]
            if test["outcome"] == "skipped":
                entry.setdefault("skip", {})[key] = test["reason"]
            if test["outcome"] != "skipped" and "steps" not in entry:
                kept = 0
                steps = []
                for step in test["steps"]:
                    frames = step["frames"][: max(0, PAGE_FRAMES - kept)]
                    kept += len(frames)
                    steps.append({"text": step["text"], "frames": frames})
                entry["steps"] = steps
                entry["via"] = key
    return {"meta": metas, "tests": [tests[node] for node in order]}


def load_mutants() -> dict[str, Any]:
    path = EVIDENCE / "mutants.json"
    if not path.exists():
        return {"meta": {}, "mutants": []}
    data: dict[str, Any] = json.loads(path.read_text())
    for mutant in data["mutants"]:
        mutant["killers"] = [{"name": k["name"], "suite": k["suite"]} for k in mutant["killers"][:6]]
    return data


# ---- the page ------------------------------------------------------------------------------------

INCLUDE = re.compile(r"<!--\s*include:\s*(\S+)\s*-->")


def assemble() -> str:
    def include(match: re.Match[str]) -> str:
        path = SITE / match.group(1)
        text = path.read_text()
        if path.suffix == ".css":
            return f"<style>\n{text}</style>"
        if path.suffix == ".js":
            return "<script>\n" + text.replace("</script", "<\\/script") + "</script>"
        return INCLUDE.sub(include, text)

    return INCLUDE.sub(include, (SITE / "shell.html").read_text())


def check_translations(run: dict[str, Any], mutants: dict[str, Any]) -> list[str]:
    ru = json.loads((SITE / "i18n" / "ru.json").read_text())
    missing = sorted({t["name"] for t in run["tests"]} - set(ru["tests"]))
    missing += sorted({m["id"] for m in mutants["mutants"]} - set(ru["mutants"]))
    missing += sorted({t["suite"] for t in run["tests"]} - set(ru["suites"]))
    patterns = [re.compile(p) for p, _ in ru["steps"]]
    texts = {s["text"] for t in run["tests"] for s in t.get("steps", []) if s["text"]}
    missing += sorted(text for text in texts if not any(p.fullmatch(text) for p in patterns))
    return missing


def main(argv: list[str]) -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    (SITE / "test").mkdir(exist_ok=True)
    run = load_run()
    mutants = load_mutants()
    (DATA / "spec.js").write_text(js("OCPP_SPEC", export_spec()))
    (DATA / "run.js").write_text(js("RUN_DATA", run))
    (DATA / "mutants.js").write_text(js("MUTANT_DATA", mutants))
    ru = json.loads((SITE / "i18n" / "ru.json").read_text())
    (DATA / "ru.js").write_text(js("RU_DATA", ru))
    (SITE / "test" / "vectors.json").write_text(json.dumps(record_vectors(), separators=(",", ":")) + "\n")
    if "--check" in argv:
        missing = check_translations(run, mutants)
        if missing:
            print("no Russian text for:\n  " + "\n  ".join(missing), file=sys.stderr)
            return 1
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(assemble())
    size = OUT.stat().st_size / 1024
    print(f"{OUT.relative_to(ROOT)}: {size:.0f} KB, {len(run['tests'])} tests, {len(mutants['mutants'])} mutants")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
