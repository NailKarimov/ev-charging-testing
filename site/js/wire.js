/* View 3: one message taken apart, and the same moment in the other version. */
(function () {
  'use strict';
  var App = window.App;
  var Twin = window.Twin;
  var h = App.h;
  var SPEC = App.SPEC;

  var version = '1.6';
  var moment = 'authorize';

  var MOMENTS = [
    { id: 'boot', en: 'The station powers on', ru: 'Станция включается' },
    { id: 'status', en: 'A cable goes into the car', ru: 'Кабель вставили в машину' },
    { id: 'authorize', en: 'A card is held to the reader', ru: 'К считывателю приложили карту' },
    { id: 'start', en: 'The session begins', ru: 'Сессия начинается' },
    { id: 'meter', en: 'A meter reading, a minute later', ru: 'Показание счётчика через минуту' },
    { id: 'stop', en: 'The session ends', ru: 'Сессия заканчивается' },
    { id: 'heartbeat', en: 'Nothing happens for five minutes', ru: 'Пять минут ничего не происходит' },
  ];

  /* Play a session and keep the first message of each moment, for one version. */
  function capture(ver) {
    var session = new Twin.Session(ver, {});
    var out = {};
    var mark = function (id) {
      var start = session.frames.length;
      return function () {
        for (var i = start; i < session.frames.length; i += 2) {
          var parsed = App.readFrame(session.frames[i][1]);
          if (parsed.kind === 2 && !out[id]) {
            out[id] = {
              request: { text: session.frames[i][1], frame: parsed },
              answer: session.frames[i + 1] ? { text: session.frames[i + 1][1], frame: App.readFrame(session.frames[i + 1][1]) } : null,
            };
            return;
          }
        }
      };
    };
    var done;
    done = mark('boot');
    session.station.boot();
    session.settle();
    done();
    done = mark('status');
    session.station.plugIn(1);
    session.settle();
    done();
    done = mark('authorize');
    session.station.presentCard('CARD-OK', 1);
    session.settle();
    done();
    // the session message comes right after the card was accepted
    var afterCard = session.frames.length;
    session.station.carReady(1, true);
    session.settle();
    for (var i = afterCard - 4; i < session.frames.length && i >= 0; i += 1) {
      var parsed = App.readFrame(session.frames[i][1]);
      if (parsed.kind === 2 && (parsed.action === 'StartTransaction' || (parsed.action === 'TransactionEvent' && parsed.payload.eventType === 'Started'))) {
        out.start = {
          request: { text: session.frames[i][1], frame: parsed },
          answer: session.frames[i + 1] ? { text: session.frames[i + 1][1], frame: App.readFrame(session.frames[i + 1][1]) } : null,
        };
        break;
      }
    }
    done = mark('meter');
    session.run(70, 70);
    done();
    done = mark('stop');
    session.station.presentCard('CARD-OK', 1);
    session.settle();
    done();
    done = mark('heartbeat');
    session.run(400, 100);
    // the heartbeat is the one named Heartbeat, not whatever came first
    for (var k = 0; k < session.frames.length; k += 1) {
      var p = App.readFrame(session.frames[k][1]);
      if (p.kind === 2 && p.action === 'Heartbeat') {
        out.heartbeat = {
          request: { text: session.frames[k][1], frame: p },
          answer: session.frames[k + 1] ? { text: session.frames[k + 1][1], frame: App.readFrame(session.frames[k + 1][1]) } : null,
        };
        break;
      }
    }
    return out;
  }

  var CAPTURED = {};

  function shots(ver) {
    if (!CAPTURED[ver]) CAPTURED[ver] = capture(ver);
    return CAPTURED[ver];
  }

  function paintAsk() {
    var box = App.clear(document.getElementById('wire-ask'));
    MOMENTS.forEach(function (m) {
      box.appendChild(
        h('button', { type: 'button', class: 'btn', 'aria-pressed': String(m.id === moment), onclick: function () {
          moment = m.id;
          paintAll();
        } }, App.t(m.en, m.ru))
      );
    });
    var versions = App.clear(document.getElementById('wire-versions'));
    ['1.6', '2.0.1'].forEach(function (name) {
      versions.appendChild(
        h('button', { type: 'button', class: 'chip', 'aria-pressed': String(name === version), onclick: function () {
          version = name;
          paintAll();
        } }, SPEC.versions[name].name)
      );
    });
  }

  function paintMessage() {
    var shot = shots(version)[moment];
    var box = App.clear(document.getElementById('wire-msg'));
    if (!shot) {
      box.appendChild(h('p', { class: 'muted' }, App.both('This moment has no message in this version.', 'В этой версии у этого момента нет сообщения.')));
      App.clear(document.getElementById('wire-parts'));
      return;
    }
    box.appendChild(h('div', { class: 'who' }, [
      App.both('The station sends:', 'Станция отправляет:'),
      h('span', { class: 'say' }, App.sayFrame(shot.request.frame)),
    ]));
    box.appendChild(App.renderWire(shot.request.frame, shot.request.text));
    if (shot.answer) {
      box.appendChild(h('div', { class: 'who', style: 'margin-top:8px' }, [
        App.both('The back office answers:', 'Облако отвечает:'),
        h('span', { class: 'say' }, App.sayFrame(shot.answer.frame)),
      ]));
      box.appendChild(App.renderWire(shot.answer.frame, shot.answer.text));
    }

    var parts = App.clear(document.getElementById('wire-parts'));
    var frame = shot.request.frame;
    parts.appendChild(
      h('div', { class: 'parts' }, [
        h('div', { class: 'part' }, [
          h('div', { class: 'k' }, '[0]'),
          h('div', { class: 'v' }, String(frame.kind)),
          h('div', { class: 'd' }, App.both('what kind of message: 2 is a question, 3 an answer, 4 a refusal', 'какого рода сообщение: 2 — вопрос, 3 — ответ, 4 — отказ')),
        ]),
        h('div', { class: 'part' }, [
          h('div', { class: 'k' }, '[1]'),
          h('div', { class: 'v' }, JSON.stringify(frame.id)),
          h('div', { class: 'd' }, App.both('the number that ties the answer to this question', 'число, которым ответ привязан к этому вопросу')),
        ]),
        h('div', { class: 'part' }, [
          h('div', { class: 'k' }, '[2]'),
          h('div', { class: 'v' }, JSON.stringify(frame.action)),
          h('div', { class: 'd' }, App.actionWords(frame.action)),
        ]),
      ])
    );
    parts.appendChild(h('div', { class: 'eyebrow', style: 'margin-top:12px' }, App.both('what is inside [3]', 'что внутри [3]')));
    parts.appendChild(App.renderParts(frame.payload));

    var note = App.clear(document.getElementById('wire-note'));
    note.appendChild(App.both(
      'Every field above is checked against the schema published with the standard. A message with a field the standard does not define, or a value outside the list it allows, is refused before the station acts on it.',
      'Каждое поле выше сверяется со схемой, опубликованной вместе со стандартом. Сообщение с полем, которого в стандарте нет, или со значением вне разрешённого списка, отклоняется до того, как станция что-то сделает.'
    ));
  }

  function paintCompare() {
    var box = App.clear(document.getElementById('wire-compare'));
    ['1.6', '2.0.1'].forEach(function (ver) {
      var shot = shots(ver)[moment];
      var card = h('div', { class: 'card' }, [h('h3', null, [SPEC.versions[ver].name, shot ? h('span', { class: 'pill' }, shot.request.frame.action) : null])]);
      if (!shot) {
        card.appendChild(h('p', { class: 'small muted' }, App.both('no message at this moment', 'в этот момент сообщения нет')));
      } else {
        card.appendChild(App.renderWire(shot.request.frame, shot.request.text));
        var rows = App.flatten(shot.request.frame.payload);
        card.appendChild(h('div', { class: 'small muted' }, App.t(rows.length + ' fields', 'полей: ' + rows.length)));
      }
      box.appendChild(card);
    });
  }

  function paintWho() {
    var box = App.clear(document.getElementById('wire-who'));
    var spec = SPEC.versions[version];
    box.appendChild(
      h('div', { class: 'card' }, [
        h('h3', null, App.t('The station says', 'Станция говорит')),
        h('div', { class: 'row' }, spec.fromStation.map(function (name) {
          return h('span', { class: 'pill sun', title: App.actionWords(name) }, name);
        })),
        h('p', { class: 'small muted' }, App.both('It reports what happens to it; it never gives orders.', 'Она сообщает о том, что с ней происходит, и никогда не отдаёт команд.')),
      ])
    );
    box.appendChild(
      h('div', { class: 'card' }, [
        h('h3', null, App.t('The back office orders', 'Облако командует')),
        h('div', { class: 'row' }, spec.orders.map(function (name) {
          return h('span', { class: 'pill', title: App.actionWords(name) }, name);
        })),
        h('p', { class: 'small muted' }, App.both(
          'An order in the standard that this station does not offer is refused as NotImplemented; one that is not in the standard at all as NotSupported.',
          'Команда из стандарта, которой у станции нет, отклоняется как NotImplemented; команда, которой в стандарте вообще нет, — как NotSupported.'
        )),
      ])
    );
  }

  function paintAll() {
    paintAsk();
    paintMessage();
    paintCompare();
    paintWho();
  }

  App.views.wire = {
    init: function () {
      paintAll();
      App.onLang(paintAll);
    },
    show: function () {},
    hide: function () {},
  };
})();
