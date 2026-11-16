/* View 2: the live station. The page plays the driver, the operator and the network at once, and a
   handful of short checks watch the station after every simulated second. */
(function () {
  'use strict';
  var App = window.App;
  var Twin = window.Twin;
  var h = App.h;

  var bugs = {};
  var version = '1.6';
  var session = null;
  var scene;
  var active = false;
  var raf = 0;
  var lastTime = 0;
  var carry = 0;
  var speed = 10;
  var scheduled = [];
  var note = null;
  var profileOn = false;
  var SPEEDS = [0, 1, 10, 60];
  var LIVE_BUGS = ['M10', 'M11', 'M13', 'M17', 'M21', 'M22', 'M25', 'M30', 'M33'];

  var checks = [
    { id: 'auth', bug: 'M10', en: 'Only a card the back office accepts starts a session', ru: 'Сессию начинает только карта, которую принимает облако', hintEn: 'Hold a blocked card to the reader.', hintRu: 'Приложите заблокированную карту.' },
    { id: 'owner', bug: 'M11', en: "Another driver's card cannot stop your session", ru: 'Чужая карта не может остановить вашу сессию', hintEn: 'Start with one card, then hold the other.', hintRu: 'Начните одной картой, потом приложите другую.' },
    { id: 'energy', bug: 'M13', en: 'The energy billed is the energy the meter counted', ru: 'В счёт идёт та энергия, что насчитал счётчик', hintEn: 'Charge for a while, then stop.', hintRu: 'Позаряжайте и остановите.' },
    { id: 'queue', bug: 'M17', en: 'Nothing is lost while the network is down', ru: 'Пока нет связи, ничего не теряется', hintEn: 'Cut the network while charging.', hintRu: 'Оборвите связь во время зарядки.' },
    { id: 'unplug', bug: 'M21', en: 'Pulling the cable out ends the session', ru: 'Если вынуть кабель, сессия заканчивается', hintEn: 'Unplug while charging.', hintRu: 'Выньте кабель во время зарядки.' },
    { id: 'suspend', bug: 'M22', en: 'A car that is not taking power is not called charging', ru: 'Машина, которая не берёт мощность, не считается заряжающейся', hintEn: 'Press "Car ready" to switch it off.', hintRu: 'Выключите «Машина готова».' },
    { id: 'lock', bug: 'M25', en: 'The cable cannot be released mid-session', ru: 'Кабель нельзя отпустить посреди сессии' },
    { id: 'expire', bug: 'M30', en: 'A limit the operator sent is obeyed', ru: 'Ограничение от оператора соблюдается', hintEn: 'Send a limit while charging.', hintRu: 'Пришлите ограничение во время зарядки.' },
    { id: 'floor', bug: 'M33', en: 'The station never gives more than it is allowed', ru: 'Станция никогда не даёт больше, чем разрешено', hintEn: 'Send a limit below 16 A.', hintRu: 'Пришлите ограничение ниже 16 А.' },
  ];
  var byId = {};
  checks.forEach(function (c) {
    c.state = 'idle';
    c.n = 0;
    c.detail = null;
    byId[c.id] = c;
  });

  function pass(id) {
    var c = byId[id];
    if (c.state !== 'fail') {
      c.state = 'pass';
      c.n += 1;
    }
  }
  function fail(id, en, ru) {
    var c = byId[id];
    if (c.state !== 'fail') {
      c.state = 'fail';
      var at = App.clock(session.station.clock, true);
      c.detail = [at + '  ' + en, at + '  ' + ru];
    }
  }
  function clearChecks(onlyBug) {
    checks.forEach(function (c) {
      if (onlyBug && c.bug !== onlyBug) return;
      c.state = 'idle';
      c.n = 0;
      c.detail = null;
    });
  }

  // ---- the rules watched after every simulated second ---------------------------------------------

  var tracker = {};

  function tick() {
    var station = session.station;
    var c = station.connector(1);
    // What the station looked like before anything happened this second.
    var before = {
      queued: station.queue.length,
      running: !!(c.transaction && c.transaction.stopped_at === null),
      transaction: c.transaction,
    };
    for (var i = scheduled.length - 1; i >= 0; i--) {
      scheduled[i].left -= 1;
      if (scheduled[i].left <= 0) {
        scheduled[i].run();
        scheduled.splice(i, 1);
      }
    }
    station.advance(1);
    session.settle();

    var amps = c.drawnA(station.clock, station.bugs);
    var allowed = c.offeredA(station.clock, station.bugs);

    // Only what the back office accepted may charge.
    if (c.transaction && c.transaction.stopped_at === null) {
      var tag = c.transaction.id_tag;
      if (Twin.KNOWN_TAGS[tag] === Twin.Auth.ACCEPTED) pass('auth');
      else fail('auth', 'a session is running on the card ' + tag, 'идёт сессия по карте ' + tag);
    }

    // The station never gives more than it is allowed.
    if (c.transaction && c.transaction.stopped_at === null && c.pilot === Twin.Pilot.C) {
      if (amps > allowed + 0.01) fail('floor', 'giving ' + amps.toFixed(1) + ' A while only ' + allowed.toFixed(1) + ' A is allowed', 'даёт ' + amps.toFixed(1) + ' А, хотя разрешено ' + allowed.toFixed(1) + ' А');
      else if (allowed < 16) pass('floor');
      if (profileOn && allowed >= Twin.MIN_CHARGE_A) {
        if (amps <= allowed + 0.01) pass('expire');
      }
    }

    // A car that is not taking power is not "charging".
    if (c.transaction && c.transaction.stopped_at === null && c.pilot !== Twin.Pilot.C) {
      if (c.status === Twin.Status.CHARGING) fail('suspend', 'the pilot says ' + c.pilot + ' and the station still reports charging', 'Pilot в состоянии ' + c.pilot + ', а станция всё равно сообщает о зарядке');
      else pass('suspend');
    }

    // Nothing is lost while the network is down: what the station owes has to pile up.
    if (!station.online && (before.running || (c.transaction && c.transaction.stopped_at === null))) {
      tracker.offline = (tracker.offline || 0) + 1;
      if (station.queue.length < before.queued) {
        fail('queue', 'something that was already kept has disappeared', 'то, что уже было отложено, исчезло');
      } else if (tracker.offline > Twin.METER_SAMPLE_S + 10) {
        if (station.queue.length > 0) pass('queue');
        else fail('queue', 'a minute with the network down and nothing has been kept', 'минута без связи, и ничего не отложено');
      }
    } else {
      tracker.offline = 0;
    }

    // Pulling the cable out has to end the session.
    if (before.running && c.pilot === Twin.Pilot.A) {
      if (before.transaction.stopped_at !== null) pass('unplug');
      else fail('unplug', 'the cable is out and the session is still running', 'кабель вынут, а сессия всё ещё идёт');
    }

    // Billing: when a session ends, what the back office will charge for must be what the meter moved.
    // The meter is read here, not taken from the session's own figures: a station that fills those in
    // wrongly would otherwise agree with itself.
    if (before.running && before.transaction.stopped_at !== null && station.online && tracker.startMeter !== undefined) {
      var row = session.sessionOf(before.transaction.id_tag);
      if (row && row.meter_stop !== null) {
        var billed = row.meter_stop - row.meter_start;
        var measured = Math.trunc(station.meter_wh) - tracker.startMeter;
        if (Math.abs(billed - measured) > 1) fail('energy', 'the back office has ' + billed + ' Wh, the meter moved ' + measured + ' Wh', 'в облаке ' + billed + ' Вт·ч, счётчик прошёл ' + measured + ' Вт·ч');
        else pass('energy');
      }
      tracker.startMeter = undefined;
    }
  }

  /* The cable comes out. A session that was running has to end with it. */
  function doUnplug() {
    var c = session.station.connector(1);
    var running = !!(c.transaction && c.transaction.stopped_at === null);
    var transaction = c.transaction;
    session.station.unplug(1);
    session.settle();
    if (running) {
      if (transaction.stopped_at !== null) pass('unplug');
      else fail('unplug', 'the cable is out and the session is still running', 'кабель вынут, а сессия всё ещё идёт');
    }
  }

  /* A card at the reader. Whoever started a session is the only one who may stop it. */
  function presentCard(tag) {
    var c = session.station.connector(1);
    var running = !!(c.transaction && c.transaction.stopped_at === null);
    var owner = running ? c.transaction.id_tag : null;
    session.station.presentCard(tag, 1);
    session.settle();
    if (running && owner !== tag) {
      if (c.transaction && c.transaction.stopped_at === null) pass('owner');
      else fail('owner', 'the card ' + tag + ' stopped a session started with ' + owner, 'карта ' + tag + ' остановила сессию, начатую картой ' + owner);
    }
    if (!running && c.transaction && c.transaction.stopped_at === null) {
      tracker.startMeter = Math.trunc(session.station.meter_wh); // the meter when this session began
    }
  }

  function probe() {
    // The cable may not be released while a car is charging.
    var station = session.station;
    var c = station.connector(1);
    if (c.transaction && c.transaction.stopped_at === null) {
      var locked = c.locked;
      var answer = station.unlock(1);
      if (answer === 'UnlockFailed') pass('lock');
      else {
        fail('lock', 'the cable was released while a car was charging', 'кабель отпустило во время зарядки машины');
        c.locked = locked;
      }
    }
  }

  // ---- what the buttons do -------------------------------------------------------------------------

  function rebuild(keepBugs) {
    if (!keepBugs) bugs = {};
    session = new Twin.Session(version, bugs);
    session.station.bugs = bugs;
    session.station.boot();
    session.settle();
    scheduled = [];
    profileOn = false;
    tracker = {};
    clearChecks();
  }

  function sendProfile(limit) {
    var c = session.station.connector(1);
    session.station.setProfile(1, { id: 1, limit_a: limit, stack_level: 0, valid_from: null, valid_to: null });
    session.settle();
    profileOn = true;
    void c;
  }
  function clearProfile() {
    session.station.clearProfiles(1, 1);
    session.settle();
    profileOn = false;
  }

  function later(ticks, run) {
    scheduled.push({ left: ticks, run: run });
  }

  function charging() {
    var c = session.station.connector(1);
    if (c.transaction && c.transaction.stopped_at === null) return;
    session.station.plugIn(1);
    presentCard('CARD-OK');
    session.station.carReady(1, true);
    session.settle();
  }

  var STIMULUS = {
    M10: function () {
      session.station.unplug(1);
      session.station.plugIn(1);
      presentCard('CARD-BLOCKED');
      session.station.carReady(1, true);
      session.settle();
      return ['The page holds a blocked card to the reader.', 'Страница прикладывает заблокированную карту.'];
    },
    M11: function () {
      charging();
      later(20, function () {
        presentCard('CARD-OK-2');
      });
      return ["The page holds a second driver's card to the reader.", 'Страница прикладывает карту второго водителя.'];
    },
    M13: function () {
      charging();
      later(120, function () {
        presentCard('CARD-OK');
      });
      return ['The page charges for a while and then stops the session.', 'Страница немного заряжает и останавливает сессию.'];
    },
    M17: function () {
      charging();
      later(20, function () {
        session.station.goOffline();
      });
      return ['The page cuts the network while the car is charging.', 'Страница обрывает связь во время зарядки.'];
    },
    M21: function () {
      charging();
      later(30, doUnplug);
      return ['The page pulls the cable out mid-charge.', 'Страница вынимает кабель посреди зарядки.'];
    },
    M22: function () {
      charging();
      later(20, function () {
        session.station.carReady(1, false);
        session.settle();
      });
      return ['The page makes the car stop taking power.', 'Страница заставляет машину перестать брать мощность.'];
    },
    M25: function () {
      charging();
      return ['The page tries to release the cable every couple of seconds.', 'Страница каждые пару секунд пытается отпустить кабель.'];
    },
    M30: function () {
      charging();
      later(15, function () {
        sendProfile(10);
      });
      return ['The page sends a limit of 10 A.', 'Страница присылает ограничение 10 А.'];
    },
    M33: function () {
      charging();
      later(15, function () {
        sendProfile(10);
      });
      return ['The page sends a limit of 10 A, below what the car asks for.', 'Страница присылает ограничение 10 А, ниже того, что просит машина.'];
    },
  };

  function setBug(id, on) {
    bugs[id] = on;
    clearChecks(id);
    scheduled = [];
    if (on) {
      if (speed === 0) setSpeed(10);
      var text = STIMULUS[id]();
      note = ['Bug ' + id + ' is on. ' + text[0], 'Баг ' + id + ' включён. ' + text[1]];
    } else {
      note = null;
      if (id === 'M17') session.station.goOnline();
    }
    paintBugs();
    paint();
  }

  // ---- painting ------------------------------------------------------------------------------------

  function $(id) {
    return document.getElementById(id);
  }

  function setSpeed(value) {
    speed = value;
    Array.prototype.forEach.call($('live-speed').children, function (chip) {
      chip.setAttribute('aria-pressed', String(Number(chip.getAttribute('data-speed')) === value));
    });
  }

  function paintBugs() {
    var box = App.clear($('live-bugs'));
    var all = window.MUTANT_DATA.mutants;
    LIVE_BUGS.forEach(function (id) {
      var mutant = all.filter(function (m) {
        return m.id === id;
      })[0] || { id: id, title: id };
      var input = h('input', {
        type: 'checkbox',
        id: 'bug-' + id,
        onchange: function () {
          setBug(id, input.checked);
        },
      });
      input.checked = !!bugs[id];
      box.appendChild(h('label', { class: 'bug' + (bugs[id] ? ' on' : ''), for: 'bug-' + id }, [input, h('span', null, [h('span', { class: 'id' }, id), App.mutantTitle(mutant)])]));
    });
    var line = $('live-note');
    line.hidden = !note;
    App.clear(line);
    if (note) line.appendChild(App.both(note[0], note[1]));
  }

  function paintChecks() {
    var box = App.clear($('live-checks'));
    checks.forEach(function (c) {
      var mark = c.state === 'pass' ? '✓' : c.state === 'fail' ? '✗' : '○';
      var detail;
      if (c.state === 'fail') detail = App.both(c.detail[0], c.detail[1]);
      else if (c.state === 'pass') detail = App.both(c.n === 1 ? 'checked once' : 'checked ' + App.num(c.n) + ' times', 'проверено раз: ' + App.num(c.n));
      else detail = c.hintEn ? App.both(c.hintEn, c.hintRu) : App.both('the page tries this every couple of seconds', 'страница пробует это каждые пару секунд');
      box.appendChild(h('div', { class: 'check ' + c.state, 'data-check': c.id }, [h('div', { class: 'mark' }, mark), h('div', { class: 't' }, App.t(c.en, c.ru)), h('div', { class: 'd' }, detail)]));
    });
  }

  function paintWire() {
    var box = App.clear($('live-wire'));
    box.style.display = 'grid';
    box.style.gap = '8px';
    var frames = session.frames.slice(-4);
    if (!frames.length) {
      box.appendChild(h('div', { class: 'say' }, App.both('nothing yet', 'пока ничего')));
      return;
    }
    frames.forEach(function (row) {
      var parsed = App.readFrame(row[1]);
      box.appendChild(
        h('div', { class: 'ln' }, [
          h('span', { class: 'dir' }, row[0] === 'tx' ? '→' : '←'),
          h('div', null, [App.renderWire(parsed, row[1]), h('div', { class: 'say' }, App.sayFrame(parsed))]),
        ])
      );
    });
  }

  function paintPilot() {
    var c = session.station.connector(1);
    var box = App.clear($('live-pilot'));
    [
      ['A', 'nothing plugged in', 'ничего не воткнуто'],
      ['B', 'cable in, not ready', 'кабель вставлен, не готова'],
      ['C', 'ready to charge', 'готова заряжаться'],
      ['E', 'fault', 'неисправность'],
    ].forEach(function (row) {
      box.appendChild(
        h('span', { class: (c.pilot === row[0] ? 'on ' : '') + (row[0] === 'E' ? 'fault' : ''), title: App.t(row[1], row[2]) }, row[0])
      );
    });
  }

  function paint() {
    var station = session.station;
    var c = station.connector(1);
    var amps = c.drawnA(station.clock, station.bugs);
    var charged = c.transaction ? Math.min(1, (station.meter_wh - c.transaction.meter_start) / 3000) : 0;
    scene.update({
      clock: station.clock,
      pilot: c.pilot,
      status: c.status,
      amps: amps,
      online: station.online,
      talking: true,
      sessions: Object.keys(session.csms.sessions).length,
      charged: charged,
      short: version === '2.0.1' ? Twin.statusName(c.status) : c.status,
    });
    $('live-led').className = 'led ' + (c.status === Twin.Status.FAULTED ? 'fault' : c.status === Twin.Status.CHARGING ? 'run' : c.status === Twin.Status.AVAILABLE ? '' : 'hold');
    $('live-state').textContent = App.statusWords(c.status);
    $('live-status').textContent = version === '1.6' ? c.status : Twin.statusName(c.status);
    $('live-version').textContent = App.SPEC.versions[version].name;
    $('live-w').textContent = App.num(Math.round(amps * Twin.NOMINAL_V)) + ' ' + App.t('W', 'Вт');
    $('live-bar').style.width = ((Math.min(1, amps / Twin.EVSE_MAX_A)) * 100).toFixed(1) + '%';
    var allowed = c.offeredA(station.clock, station.bugs);
    var mark = $('live-mark');
    mark.style.display = profileOn ? '' : 'none';
    mark.style.left = 'calc(' + ((allowed / Twin.EVSE_MAX_A) * 100).toFixed(1) + '% - 1px)';
    $('live-a').textContent = App.num(amps, 1) + ' ' + App.t('A', 'А');
    $('live-limit-v').textContent = profileOn ? App.num(allowed, 0) + ' ' + App.t('A', 'А') : App.t('no limit', 'без ограничения');
    var energy = c.transaction ? (c.transaction.meter_stop === null ? Math.trunc(station.meter_wh) : c.transaction.meter_stop) - c.transaction.meter_start : 0;
    $('live-session').textContent = App.num(energy) + ' ' + App.t('Wh', 'Вт·ч');
    $('live-sessions').textContent = String(Object.keys(session.csms.sessions).length);
    $('live-ready').setAttribute('aria-pressed', String(c.pilot === Twin.Pilot.C));
    $('live-offline').setAttribute('aria-pressed', String(!station.online));
    $('live-profile').setAttribute('aria-pressed', String(profileOn));
    $('live-limit-label').textContent = $('live-limit').value + ' ' + App.t('A', 'А');
    paintPilot();
    paintChecks();
    paintWire();
  }

  var sinceProbe = 0;

  function loop(now) {
    if (!active) return;
    var dt = Math.min(0.25, (now - lastTime) / 1000);
    lastTime = now;
    carry += dt * speed;
    var ticks = Math.min(600, Math.floor(carry));
    carry -= ticks;
    for (var i = 0; i < ticks; i++) tick();
    sinceProbe += dt;
    if (sinceProbe > 1.5) {
      sinceProbe = 0;
      probe();
    }
    paint();
    raf = window.requestAnimationFrame(loop);
  }

  function wire() {
    var speeds = $('live-speed');
    SPEEDS.forEach(function (value) {
      speeds.appendChild(
        h('button', { type: 'button', class: 'chip', 'data-speed': value, 'aria-pressed': String(value === speed), onclick: function () { setSpeed(value); } },
          value === 0 ? App.both('pause', 'пауза') : '×' + value)
      );
    });
    var versions = $('live-versions');
    ['1.6', '2.0.1'].forEach(function (name) {
      versions.appendChild(
        h('button', { type: 'button', class: 'chip', 'data-version': name, 'aria-pressed': String(name === version), onclick: function () {
          version = name;
          Array.prototype.forEach.call(versions.children, function (chip) {
            chip.setAttribute('aria-pressed', String(chip.getAttribute('data-version') === version));
          });
          rebuild(true);
          paint();
        } }, App.SPEC.versions[name].name)
      );
    });
    var cards = $('live-cards');
    App.SPEC.cards.forEach(function (card) {
      cards.appendChild(
        h('button', { type: 'button', class: 'chip', onclick: function () {
          presentCard(card);
          paint();
        } }, card)
      );
    });
    $('live-plug').addEventListener('click', function () {
      session.station.plugIn(1);
      session.settle();
      paint();
    });
    $('live-unplug').addEventListener('click', function () {
      doUnplug();
      paint();
    });
    $('live-ready').addEventListener('click', function () {
      var c = session.station.connector(1);
      session.station.carReady(1, c.pilot !== Twin.Pilot.C);
      session.settle();
      paint();
    });
    var limit = $('live-limit');
    limit.addEventListener('input', function () {
      $('live-limit-label').textContent = limit.value + ' ' + App.t('A', 'А');
      if (profileOn) sendProfile(Number(limit.value));
    });
    $('live-profile').addEventListener('click', function () {
      if (profileOn) clearProfile();
      else sendProfile(Number(limit.value));
      paint();
    });
    $('live-remote').addEventListener('click', function () {
      var c = session.station.connector(1);
      if (c.transaction && c.transaction.stopped_at === null) {
        session.station.stopTransaction(1, Twin.Reason.REMOTE);
        session.settle();
      }
      paint();
    });
    $('live-offline').addEventListener('click', function () {
      if (session.station.online) session.station.goOffline();
      else {
        session.station.goOnline();
        session.settle();
      }
      paint();
    });
    $('live-reset').addEventListener('click', function () {
      rebuild(false);
      note = null;
      setSpeed(10);
      paintBugs();
      paint();
    });
  }

  App.views.live = {
    init: function () {
      scene = App.Scene($('live-scene'));
      rebuild(false);
      wire();
      paintBugs();
      paint();
      App.onLang(function () {
        paintBugs();
        paint();
      });
    },
    show: function () {
      active = true;
      lastTime = window.performance.now();
      raf = window.requestAnimationFrame(loop);
    },
    hide: function () {
      active = false;
      if (raf) window.cancelAnimationFrame(raf);
    },
    debug: {
      setBug: setBug,
      tick: tick,
      probe: probe,
      checks: checks,
      session: function () {
        return session;
      },
    },
  };
})();
