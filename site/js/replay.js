/* View 4: the last test run. Suites, tests in plain words, and the frames each test exchanged. */
(function () {
  'use strict';
  var App = window.App;
  var h = App.h;
  var run = window.RUN_DATA;
  var LINKS = ['1.6-memory', '2.0.1-memory', '1.6-websocket', '2.0.1-websocket'];
  var SHORT = { '1.6-memory': '1.6', '2.0.1-memory': '2.0.1', '1.6-websocket': '1.6 ws', '2.0.1-websocket': '2.0.1 ws' };
  var LINK_TEXT = {
    '1.6-memory': ['OCPP 1.6', 'OCPP 1.6', 'The version running on most charge points in the street, exchanged inside one program.', 'Версия, которая работает на большинстве станций на улице; обмен внутри одной программы.'],
    '2.0.1-memory': ['OCPP 2.0.1', 'OCPP 2.0.1', 'The version new charge points are certified against, exchanged inside one program.', 'Версия, по которой сертифицируют новые станции; обмен внутри одной программы.'],
    '1.6-websocket': ['1.6 over a real connection', '1.6 по настоящему соединению', 'The handshake, real frames and a connection cut in the middle.', 'Рукопожатие, настоящие кадры и оборванное посреди сессии соединение.'],
    '2.0.1-websocket': ['2.0.1 over a real connection', '2.0.1 по настоящему соединению', 'The same, with the other version of the protocol.', 'То же самое, но на другой версии протокола.'],
  };
  var SKIP_RU = {
    'does not depend on the version: runs once, in the OCPP 1.6 run': 'не зависит от версии: выполняется один раз, в прогоне OCPP 1.6',
    'OCPP 1.6 only': 'только для OCPP 1.6',
    'OCPP 2.0.1 only': 'только для OCPP 2.0.1',
    'needs a real WebSocket: run with LINK=websocket': 'нужно настоящее соединение WebSocket',
    'the third-party library connects over a WebSocket: run with LINK=websocket': 'сторонняя библиотека подключается по WebSocket',
    'the behaviour is covered by the in-memory runs; this run tests the transport': 'поведение покрыто прогонами в памяти; этот прогон проверяет соединение',
  };
  var query = '';
  var open = {};

  function overall(test) {
    var result = 'skipped';
    LINKS.forEach(function (link) {
      var o = test.runs[link] && test.runs[link].o;
      if (o === 'f') result = 'failed';
      else if (o === 'p' && result !== 'failed') result = 'passed';
    });
    return result;
  }

  function linkCards() {
    var box = App.clear(document.getElementById('replay-links'));
    LINKS.forEach(function (link) {
      var meta = run.meta[link];
      if (!meta) return;
      var text = LINK_TEXT[link];
      box.appendChild(
        h('div', { class: 'card' }, [
          h('div', { class: 'eyebrow' }, SHORT[link]),
          h('h3', null, App.t(text[0], text[1])),
          h('div', { class: 'big' }, [App.num(meta.passed), ' ', h('span', { class: 'pill ' + (meta.failed ? 'bad' : 'ok') }, meta.failed ? App.t(meta.failed + ' failed', 'упало: ' + meta.failed) : App.t('all passed', 'все прошли'))]),
          h('div', { class: 'small muted' }, App.t(meta.skipped + ' skipped · ' + App.num(meta.frames) + ' messages exchanged', 'пропущено: ' + meta.skipped + ' · сообщений: ' + App.num(meta.frames))),
          h('p', { class: 'small muted' }, App.t(text[2], text[3])),
        ])
      );
    });
  }

  function frameRows(box, direction, text) {
    var frame = App.readFrame(text);
    box.appendChild(
      h('div', { class: 'fr ' + direction }, [
        h('span', { class: 'dir', title: direction === 'tx' ? App.t('from the station', 'от станции') : App.t('to the station', 'станции') }, direction === 'tx' ? '→' : '←'),
        App.renderWire(frame, text),
        h('div', { class: 'say' }, App.sayFrame(frame)),
      ])
    );
  }

  function detail(test) {
    var box = h('div', { class: 'detail' });
    var shown = 0;
    var steps = test.steps || [];
    steps.forEach(function (step) {
      if (step.text) box.appendChild(h('div', { class: 'step' }, App.stepText(step.text)));
      step.frames.forEach(function (frame) {
        shown += 1;
        frameRows(box, frame[0], frame[1]);
      });
    });
    if (test.via) {
      var total = test.runs[test.via].n;
      if (shown === 0 && !steps.length) box.appendChild(h('p', { class: 'small muted' }, App.t('This check works on the rules directly and exchanges no messages with a station.', 'Эта проверка работает прямо с правилами и не обменивается сообщениями со станцией.')));
      if (total > shown && shown > 0) box.appendChild(h('p', { class: 'small muted' }, App.t('…and ' + App.num(total - shown) + ' more messages in this check.', '…и ещё сообщений в этой проверке: ' + App.num(total - shown) + '.')));
      if (shown > 0) box.appendChild(h('p', { class: 'small muted' }, App.t('Recorded in the run: ', 'Записано в прогоне: ') + App.t(LINK_TEXT[test.via][0], LINK_TEXT[test.via][1]) + '. → ' + App.t('from the station', 'от станции') + ', ← ' + App.t('to the station', 'станции') + '.'));
    }
    LINKS.forEach(function (link) {
      var info = test.runs[link];
      if (info && info.why) box.appendChild(h('p', { class: 'small', style: 'color:var(--bad)' }, SHORT[link] + ': ' + info.why));
      var reason = test.skip && test.skip[link];
      if (reason) box.appendChild(h('p', { class: 'small muted' }, SHORT[link] + ' — ' + App.t('skipped: ', 'пропущена: ') + (App.lang === 'ru' && SKIP_RU[reason] ? SKIP_RU[reason] : reason)));
    });
    box.appendChild(h('p', { class: 'small muted mono' }, 'tests/' + test.suite + '.py::' + test.name + (test.params ? '[' + test.params + ']' : '')));
    return box;
  }

  function testRow(test) {
    var result = overall(test);
    var row = h('div', { class: 'test ' + result });
    var chips = h('span', { class: 'lk' });
    LINKS.forEach(function (link) {
      var info = test.runs[link];
      if (info) chips.appendChild(h('i', { class: info.o }, SHORT[link]));
    });
    var button = h(
      'button',
      {
        type: 'button',
        'aria-expanded': 'false',
        onclick: function () {
          var existing = row.querySelector('.detail');
          if (existing) {
            row.removeChild(existing);
            button.setAttribute('aria-expanded', 'false');
          } else {
            row.appendChild(detail(test));
            button.setAttribute('aria-expanded', 'true');
          }
        },
      },
      [h('span', { class: 'mk' }, result === 'passed' ? '✓' : result === 'failed' ? '✗' : '–'), h('span', null, [App.testTitle(test), test.params ? h('span', { class: 'par' }, test.params) : null]), chips]
    );
    row.appendChild(button);
    return row;
  }

  function matches(test) {
    if (!query) return true;
    var hay = (App.testTitle(test) + ' ' + test.title + ' ' + test.name + ' ' + test.params).toLowerCase();
    return hay.indexOf(query) >= 0;
  }

  function suites() {
    var box = App.clear(document.getElementById('replay-suites'));
    var order = Object.keys(App.RU.suites);
    var bySuite = {};
    run.tests.forEach(function (test) {
      (bySuite[test.suite] = bySuite[test.suite] || []).push(test);
    });
    Object.keys(bySuite).forEach(function (suite) {
      if (order.indexOf(suite) < 0) order.push(suite);
    });
    var any = false;
    order.forEach(function (suite) {
      var tests = (bySuite[suite] || []).filter(matches);
      if (!tests.length) return;
      any = true;
      var passed = tests.filter(function (t) {
        return overall(t) === 'passed';
      }).length;
      var failed = tests.filter(function (t) {
        return overall(t) === 'failed';
      }).length;
      var skipped = tests.length - passed - failed;
      var label = failed ? App.t(failed + ' failed', 'упало: ' + failed) : passed + ' / ' + tests.length;
      var list = h('div', { class: 'tests' });
      var filled = false;
      function fill() {
        if (filled) return;
        filled = true;
        tests.forEach(function (test) {
          list.appendChild(testRow(test));
        });
      }
      var details = h('details', { class: 'suite', 'data-suite': suite }, [
        h('summary', null, [h('span', { class: 'nm' }, App.suiteName(suite)), h('span', { class: 'bl' }, App.suiteBlurb(suite)), h('span', { class: 'ct pill ' + (failed ? 'bad' : skipped === tests.length ? '' : 'ok') }, label)]),
        list,
      ]);
      details.addEventListener('toggle', function () {
        open[suite] = details.open;
        if (details.open) fill();
      });
      if (query || open[suite]) {
        details.open = true;
        fill();
      }
      box.appendChild(details);
    });
    document.getElementById('replay-none').hidden = any;
  }

  function legend() {
    var box = App.clear(document.getElementById('replay-legend'));
    LINKS.forEach(function (key) {
      box.appendChild(App.h('span', null, [App.h('i', { class: 'p' }, SHORT[key]), App.t(LINK_TEXT[key][0], LINK_TEXT[key][1])]));
    });
    box.appendChild(App.h('span', null, [App.h('i', null, SHORT[LINKS[0]]), App.t('grey: skipped in that run', 'серый: в том прогоне пропущена')]));
  }

  App.views.replay = {
    init: function () {
      var search = document.getElementById('replay-search');
      function placeholder() {
        search.setAttribute('placeholder', App.t('Search: card, limit, offline…', 'Поиск: карта, ограничение, связь…'));
      }
      var timer = 0;
      search.addEventListener('input', function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(function () {
          query = search.value.trim().toLowerCase();
          suites();
        }, 150);
      });
      open.test_offline = true;
      placeholder();
      legend();
      linkCards();
      suites();
      App.onLang(function () {
        placeholder();
        legend();
        linkCards();
        suites();
      });
    },
    show: function () {},
    hide: function () {},
    /* open one suite and scroll to it (used by links from the bug hunt) */
    find: function (text) {
      var search = document.getElementById('replay-search');
      search.value = text;
      query = text.toLowerCase();
      suites();
    },
  };
})();
