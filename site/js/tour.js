/* View 1: the project in two minutes. */
(function () {
  'use strict';
  var App = window.App;
  var Twin = window.Twin;
  var h = App.h;
  var SPEC = App.SPEC;
  var session, scene, timer, active = false, phase = 0, hold = 0;
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // A loop the page plays by itself: plug in, card, charge, leave, start again.
  var SCRIPT = [
    { at: 0, run: function (s) { s.station.plugIn(1); } },
    { at: 3, run: function (s) { s.station.presentCard('CARD-OK', 1); } },
    { at: 5, run: function (s) { s.station.carReady(1, true); } },
    { at: 34, run: function (s) { s.station.presentCard('CARD-OK', 1); } },
    { at: 37, run: function (s) { s.station.unplug(1); } },
    { at: 42, run: function (s) { restart(s); } },
  ];

  function restart() {
    session = new Twin.Session('1.6', {});
    session.station.boot();
    session.settle();
    phase = 0;
  }

  function paintScene() {
    var station = session.station;
    var c = station.connector(1);
    var amps = c.drawnA(station.clock, station.bugs);
    var charged = c.transaction ? Math.min(1, (station.meter_wh - c.transaction.meter_start) / 2000) : 0;
    scene.update({
      clock: station.clock,
      pilot: c.pilot,
      status: c.status,
      amps: amps,
      online: true,
      talking: session.frames.length > 0,
      sessions: Object.keys(session.csms.sessions).length,
      charged: charged,
      short: c.status,
    });
    var line = document.getElementById('tour-now');
    line.textContent =
      App.clock(station.clock) + '  ' + App.statusWords(c.status) + (amps > 0 ? '  ' + App.num(Math.round(amps * Twin.NOMINAL_V)) + ' ' + App.t('W', 'Вт') : '');
  }

  function frame() {
    if (!active) return;
    hold += 1;
    if (hold >= 20) {
      hold = 0;
      var step = SCRIPT[phase];
      if (step) {
        step.run(session);
        session.settle();
        phase += 1;
      }
    }
    session.run(6, 6);
    paintScene();
    timer = window.requestAnimationFrame(frame);
  }

  function talk() {
    var box = App.clear(document.getElementById('tour-talk'));
    var demo = new Twin.Session('1.6', {});
    demo.station.boot();
    demo.settle();
    demo.station.plugIn(1);
    demo.settle();
    demo.station.presentCard('CARD-OK', 1);
    demo.settle();
    var asked = null;
    var answered = null;
    for (var i = 0; i < demo.frames.length; i++) {
      var parsed = App.readFrame(demo.frames[i][1]);
      if (parsed.action === 'Authorize') {
        asked = { text: demo.frames[i][1], frame: parsed };
        answered = { text: demo.frames[i + 1][1], frame: App.readFrame(demo.frames[i + 1][1]) };
        break;
      }
    }
    if (!asked) return;
    box.appendChild(
      h('div', { class: 'q' }, [
        h('div', { class: 'who' }, [
          App.both('The station asks:', 'Станция спрашивает:'),
          h('span', { class: 'say' }, App.both('"May the card CARD-OK charge?"', '«Карте CARD-OK можно заряжаться?»')),
        ]),
        App.renderWire(asked.frame, asked.text),
      ])
    );
    box.appendChild(
      h('div', { class: 'a' }, [
        h('div', { class: 'who' }, [
          App.both('The back office answers:', 'Облако отвечает:'),
          h('span', { class: 'say' }, App.both('"Yes."', '«Да».')),
        ]),
        App.renderWire(answered.frame, answered.text),
        h('p', { class: 'small muted' }, App.both(
          'The number in the middle is the same in both: that is how an answer is tied to its question.',
          'Число посередине в обоих одно и то же: так ответ привязан к своему вопросу.'
        )),
      ])
    );
  }

  function versions() {
    var box = App.clear(document.getElementById('tour-versions'));
    [
      ['1.6', 'Most charge points in the street', 'Большинство станций на улице', 'A session is three separate messages, and the back office hands out its number.', 'Сессия — три отдельных сообщения, и номер ей даёт облако.'],
      ['2.0.1', 'What new ones are certified against', 'По ней сертифицируют новые', 'A session is one message with Started, Updated and Ended, numbered in order, and the station names it itself.', 'Сессия — одно сообщение с Started, Updated и Ended, пронумерованными по порядку, и имя ей даёт сама станция.'],
    ].forEach(function (row) {
      var spec = SPEC.versions[row[0]];
      box.appendChild(
        h('div', { class: 'card' }, [
          h('h3', null, [spec.name, h('span', { class: 'pill' }, spec.subprotocol)]),
          h('p', { class: 'small muted' }, App.t(row[1], row[2])),
          h('p', { class: 'small' }, App.t(row[3], row[4])),
          h('div', { class: 'small muted' }, App.t(
            spec.messages.length + ' messages in the standard, ' + spec.fromStation.length + ' of them sent by the station',
            'в стандарте сообщений: ' + spec.messages.length + ', из них станция шлёт ' + spec.fromStation.length
          )),
          h('div', { class: 'row' }, spec.fromStation.map(function (name) {
            return h('span', { class: 'pill' }, name);
          })),
        ])
      );
    });
  }

  function tile(big, en, ru) {
    return h('div', { class: 'tile' }, [h('div', { class: 'big' }, big), h('div', { class: 'what' }, App.both(en, ru))]);
  }

  function numbers() {
    var run = window.RUN_DATA;
    var box = App.clear(document.getElementById('tour-tiles'));
    var passed = 0, failed = 0, frames = 0, runs = 0;
    Object.keys(run.meta).forEach(function (key) {
      runs += 1;
      passed += run.meta[key].passed;
      failed += run.meta[key].failed;
      frames += run.meta[key].frames;
    });
    box.appendChild(tile(App.num(run.tests.length), 'checks, each one a plain sentence', 'проверок, каждая — понятное предложение'));
    box.appendChild(tile(String(runs), 'runs: two versions of OCPP, in memory and over a real connection', 'прогона: две версии OCPP, в памяти и по настоящему соединению'));
    box.appendChild(tile(App.num(passed), 'passes in the last run, ' + failed + ' failures', 'прохождений в последнем прогоне, падений: ' + failed));
    box.appendChild(tile(App.num(frames), 'messages exchanged with the station', 'сообщений, которыми обменялись со станцией'));

    var total = SPEC.versions['1.6'].schemaCount + SPEC.versions['2.0.1'].schemaCount;
    App.clear(document.getElementById('tour-schemas')).appendChild(
      App.both(
        'The schemas published with the standard: ' + total + ' messages in the two versions. Every message this project sends or receives is checked against them.',
        'Схемы, опубликованные со стандартом: ' + total + ' сообщений в двух версиях. Каждое сообщение, которое проект шлёт или получает, сверяется с ними.'
      )
    );

    var hunt = window.MUTANT_DATA;
    var dots = App.clear(document.getElementById('tour-dots'));
    hunt.mutants.forEach(function (m) {
      dots.appendChild(h('i', { class: m.caught ? null : 'bad', title: m.id }));
    });
    var line = App.clear(document.getElementById('tour-hunt'));
    line.appendChild(h('strong', null, App.both(
      hunt.meta.caught + ' of ' + hunt.meta.total + ' planted bugs were caught.',
      'Поймано ' + hunt.meta.caught + ' из ' + hunt.meta.total + ' посаженных багов.'
    )));
    line.appendChild(document.createTextNode(' '));
    line.appendChild(h('span', { class: 'muted' }, App.both('Each square is one bug.', 'Каждый квадрат — один баг.')));
  }

  App.views.tour = {
    init: function () {
      scene = App.Scene(document.getElementById('tour-scene'));
      restart();
      if (reduced) {
        session.station.plugIn(1);
        session.station.presentCard('CARD-OK', 1);
        session.station.carReady(1, true);
        session.run(300, 60);
      }
      talk();
      versions();
      numbers();
      paintScene();
      App.onLang(function () {
        talk();
        versions();
        numbers();
        paintScene();
      });
    },
    show: function () {
      active = true;
      if (!reduced) timer = window.requestAnimationFrame(frame);
    },
    hide: function () {
      active = false;
      if (timer) window.cancelAnimationFrame(timer);
    },
  };
})();
