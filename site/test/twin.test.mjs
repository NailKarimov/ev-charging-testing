// Replays conversations recorded from the Python station against the JavaScript twin.
// Every message must be identical. Run: node site/test/twin.test.mjs
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(readFileSync(join(here, '../js/twin.js'), 'utf8'), context, { filename: 'twin.js' });
const Twin = context.window.Twin;
const scenarios = JSON.parse(readFileSync(join(here, 'vectors.json'), 'utf8'));

function play(version, steps) {
  const session = new Twin.Session(version, {});
  const station = session.station;
  for (const step of steps) {
    const [name] = step;
    if (name === 'boot') station.boot();
    else if (name === 'plug') station.plugIn(step[1]);
    else if (name === 'unplug') station.unplug(step[1]);
    else if (name === 'ready') station.carReady(step[1], step[2]);
    else if (name === 'card') station.presentCard(step[1], step[2]);
    else if (name === 'fault') { station.connector(step[1]).pilot = Twin.Pilot.E; station.advance(1); }
    else if (name === 'offline') station.goOffline();
    else if (name === 'online') station.goOnline();
    else if (name === 'profile') station.setProfile(step[1], { id: step[3], limit_a: step[2], stack_level: step[4], valid_from: null, valid_to: null });
    else if (name === 'clear') station.clearProfiles(step[1], step[2]);
    else if (name === 'order') {
      if (step[1] === 'unlock') station.unlock(step[2]);
      else if (step[1] === 'operative') station.setOperative(step[2], step[3]);
    } else if (name === 'run') { session.run(step[1], step[2]); continue; }
    else throw new Error('unknown step ' + name);
    session.settle();
  }
  return session.frames;
}

// The messages are compared as JSON, not as text. JavaScript has one number type, so it writes 3680
// where Python writes 3680.0; that is how the number is spelled, not what the message says. Where the
// standard asks for text, as OCPP 1.6 does for a meter reading, the text itself must match exactly.
function difference(a, b, path = '') {
  if (a === b) return null;
  if (typeof a !== typeof b || a === null || b === null) return `${path || '(root)'}: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`;
  if (typeof a === 'number') return Math.abs(a - b) < 1e-9 ? null : `${path}: ${a} vs ${b}`;
  if (Array.isArray(a) !== Array.isArray(b)) return `${path}: one is a list, the other is not`;
  if (typeof a !== 'object') return `${path}: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    const found = difference(a[key], b[key], path ? `${path}.${key}` : key);
    if (found) return found;
  }
  return null;
}

let messages = 0;
let failures = 0;
for (const scenario of scenarios) {
  const mine = play(scenario.version, scenario.steps);
  const theirs = scenario.frames;
  const limit = Math.max(mine.length, theirs.length);
  for (let i = 0; i < limit; i++) {
    messages += 1;
    if (!mine[i] || !theirs[i]) {
      failures += 1;
      console.error(`MISMATCH in "${scenario.name}" (OCPP ${scenario.version}): the twin sent ${mine.length} messages, Python sent ${theirs.length}`);
      break;
    }
    const found =
      mine[i][0] !== theirs[i][0]
        ? `direction: ${mine[i][0]} vs ${theirs[i][0]}`
        : difference(JSON.parse(mine[i][1]), JSON.parse(theirs[i][1]));
    if (found) {
      failures += 1;
      if (failures <= 4) {
        console.error(`MISMATCH in "${scenario.name}" (OCPP ${scenario.version}), message ${i + 1}: ${found}\n  python  ${theirs[i][1]}\n  twin    ${mine[i][1]}`);
      }
      break; // one scenario, one report: everything after a divergence follows from it
    }
  }
}
if (failures) {
  console.error(`${failures} of ${scenarios.length} conversations differ from the Python station`);
  process.exit(1);
}
console.log(`twin: ${messages} messages in ${scenarios.length} conversations, all identical to the Python station`);
