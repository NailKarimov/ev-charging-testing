/* Shared helpers: language, a small DOM builder, number formats, and the reader that turns one OCPP
   message into coloured parts and a plain sentence. */
(function () {
  'use strict';
  var Twin = window.Twin;
  var SPEC = window.OCPP_SPEC;
  var App = (window.App = { views: {}, lang: 'en', SPEC: SPEC });

  // ---- language ---------------------------------------------------------------------------------

  var langListeners = [];
  App.onLang = function (fn) {
    langListeners.push(fn);
  };
  App.t = function (en, ru) {
    return App.lang === 'ru' ? ru : en;
  };
  App.setLang = function (lang, silent) {
    App.lang = lang === 'ru' ? 'ru' : 'en';
    var html = document.documentElement;
    html.setAttribute('data-lang', App.lang);
    html.setAttribute('lang', App.lang);
    document.getElementById('lang-en').setAttribute('aria-pressed', String(App.lang === 'en'));
    document.getElementById('lang-ru').setAttribute('aria-pressed', String(App.lang === 'ru'));
    try {
      localStorage.setItem('lang', App.lang);
    } catch (e) {
      /* storage is optional */
    }
    if (!silent)
      langListeners.forEach(function (fn) {
        fn();
      });
  };
  App.initialLang = function () {
    var saved = null;
    try {
      saved = localStorage.getItem('lang');
    } catch (e) {
      /* storage is optional */
    }
    if (saved === 'ru' || saved === 'en') return saved;
    return /^ru/i.test(navigator.language || '') ? 'ru' : 'en';
  };

  // ---- DOM ----------------------------------------------------------------------------------------

  App.h = function (tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs)
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), value);
        else if (key === 'html') node.innerHTML = value;
        else node.setAttribute(key, value === true ? '' : value);
      });
    (Array.isArray(children) ? children : children === undefined ? [] : [children]).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
    });
    return node;
  };
  App.clear = function (node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  };
  App.both = function (en, ru) {
    var frag = document.createDocumentFragment();
    frag.appendChild(App.h('span', { class: 'en' }, en));
    frag.appendChild(App.h('span', { class: 'ru' }, ru));
    return frag;
  };

  // ---- formats ------------------------------------------------------------------------------------

  App.num = function (value, digits) {
    return Number(value).toLocaleString(App.lang === 'ru' ? 'ru-RU' : 'en-US', {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits || 0,
    });
  };
  App.clock = function (seconds, withSeconds) {
    var s = ((seconds % 86400) + 86400) % 86400;
    var hh = Math.floor(s / 3600);
    var mm = Math.floor((s % 3600) / 60);
    var text = (hh < 10 ? '0' : '') + hh + ':' + (mm < 10 ? '0' : '') + mm;
    if (withSeconds) text += ':' + (s % 60 < 10 ? '0' : '') + (s % 60);
    return text;
  };
  App.plural = function (n, one, few, many) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  };

  // ---- what each message and each field means ---------------------------------------------------------

  var ACTION = {
    BootNotification: ['Hello, here is who I am', 'Здравствуйте, вот кто я'],
    StatusNotification: ['This is what my connector is doing now', 'Вот что сейчас с моим коннектором'],
    Authorize: ['May this card charge?', 'Этой карте можно заряжаться?'],
    StartTransaction: ['A session has begun', 'Сессия началась'],
    MeterValues: ['Here is the meter reading', 'Вот показание счётчика'],
    StopTransaction: ['The session has ended', 'Сессия закончилась'],
    TransactionEvent: ['Something happened in a session', 'В сессии что-то произошло'],
    Heartbeat: ['I am still here', 'Я на связи'],
    RemoteStartTransaction: ['Start charging this car', 'Начни заряжать эту машину'],
    RequestStartTransaction: ['Start charging this car', 'Начни заряжать эту машину'],
    RemoteStopTransaction: ['Stop that session', 'Останови эту сессию'],
    RequestStopTransaction: ['Stop that session', 'Останови эту сессию'],
    SetChargingProfile: ['From now on give no more than this', 'С этого момента давай не больше этого'],
    ClearChargingProfile: ['Forget that limit', 'Забудь то ограничение'],
    ChangeAvailability: ['Take this connector in or out of service', 'Выведи коннектор из работы или верни в работу'],
    UnlockConnector: ['Release the cable', 'Отпусти кабель'],
    Reset: ['Restart yourself', 'Перезапустись'],
    TriggerMessage: ['Send me that message now', 'Пришли мне это сообщение сейчас'],
    ChangeConfiguration: ['Change this setting', 'Измени эту настройку'],
    GetConfiguration: ['Tell me your settings', 'Расскажи свои настройки'],
    SetVariables: ['Change this setting', 'Измени эту настройку'],
    GetVariables: ['Tell me your settings', 'Расскажи свои настройки'],
    DataTransfer: ['Something outside the standard', 'Что-то вне стандарта'],
  };

  var FIELD = {
    chargePointVendor: ['who made the station', 'кто сделал станцию'],
    chargePointModel: ['which model it is', 'какая это модель'],
    chargePointSerialNumber: ['its serial number', 'её серийный номер'],
    firmwareVersion: ['the software it runs', 'версия её программы'],
    chargingStation: ['who the station is', 'что это за станция'],
    reason: ['why', 'почему'],
    connectorId: ['which socket', 'какой разъём'],
    evseId: ['which socket', 'какой разъём'],
    errorCode: ['any fault', 'есть ли неисправность'],
    status: ['the answer', 'ответ'],
    connectorStatus: ['what the socket is doing', 'что с разъёмом'],
    timestamp: ['when', 'когда'],
    currentTime: ['the time at the back office', 'время в облаке'],
    interval: ['how often to call in, in seconds', 'как часто выходить на связь, в секундах'],
    idTag: ['the number on the card', 'номер карты'],
    idToken: ['the number on the card', 'номер карты'],
    idTagInfo: ['what the back office says about the card', 'что облако говорит о карте'],
    idTokenInfo: ['what the back office says about the card', 'что облако говорит о карте'],
    meterStart: ['the meter when it began, in watt-hours', 'счётчик в начале, в ватт-часах'],
    meterStop: ['the meter when it ended, in watt-hours', 'счётчик в конце, в ватт-часах'],
    transactionId: ['the number of this session', 'номер этой сессии'],
    transactionInfo: ['which session', 'какая сессия'],
    meterValue: ['the meter reading', 'показание счётчика'],
    sampledValue: ['what was measured', 'что измерено'],
    eventType: ['what kind of event', 'какое событие'],
    triggerReason: ['what caused it', 'что его вызвало'],
    seqNo: ['the number of this event in the session', 'номер события в сессии'],
    offline: ['it happened while the link was down', 'произошло, пока не было связи'],
    evse: ['which socket', 'какой разъём'],
    chargingState: ['what the car is doing', 'что делает машина'],
    stoppedReason: ['why it ended', 'почему закончилась'],
    csChargingProfiles: ['the limit', 'ограничение'],
    chargingProfile: ['the limit', 'ограничение'],
    chargingSchedule: ['the plan', 'план'],
    chargingSchedulePeriod: ['from when, and how much', 'с какого момента и сколько'],
    limit: ['the most it may give, in amps', 'больше этого давать нельзя, в амперах'],
    chargingRateUnit: ['measured in amps or watts', 'в амперах или в ваттах'],
    stackLevel: ['which limit wins', 'какое ограничение главнее'],
    type: ['which kind', 'какого вида'],
    operationalStatus: ['in service or out of service', 'в работе или выведен'],
    requestedMessage: ['which message', 'какое сообщение'],
    key: ['the name of the setting', 'название настройки'],
    value: ['the value', 'значение'],
    measurand: ['what is measured', 'что измеряется'],
    unit: ['the unit', 'единица'],
    unitOfMeasure: ['the unit', 'единица'],
    context: ['why this reading was taken', 'почему снято это показание'],
  };

  App.actionWords = function (action) {
    var row = ACTION[action];
    return row ? App.t(row[0], row[1]) : '';
  };
  App.fieldWords = function (name) {
    var row = FIELD[name];
    return row ? App.t(row[0], row[1]) : '';
  };

  var VALUE = {
    Accepted: ['yes', 'да'],
    Rejected: ['no', 'нет'],
    Blocked: ['this card is blocked', 'карта заблокирована'],
    Expired: ['this card has expired', 'срок карты истёк'],
    Invalid: ['no such card', 'такой карты нет'],
    Unknown: ['no such card', 'такой карты нет'],
    ConcurrentTx: ['that card is already charging something else', 'эта карта уже заряжает другое'],
    Available: ['free', 'свободен'],
    Preparing: ['a cable is in, waiting for a card', 'кабель вставлен, ждёт карту'],
    Charging: ['charging', 'идёт зарядка'],
    SuspendedEV: ['paused: the car is not taking power', 'пауза: машина не берёт мощность'],
    SuspendedEVSE: ['paused: the station is not giving power', 'пауза: станция не даёт мощность'],
    Finishing: ['done, the cable is still in', 'закончено, кабель ещё вставлен'],
    Occupied: ['busy', 'занят'],
    Unavailable: ['out of service', 'выведен из работы'],
    Faulted: ['faulty', 'неисправен'],
    Started: ['a session started', 'сессия началась'],
    Updated: ['a session is running', 'сессия идёт'],
    Ended: ['a session ended', 'сессия закончилась'],
    Local: ['the driver stopped it', 'остановил водитель'],
    Remote: ['the operator stopped it', 'остановил оператор'],
    EVDisconnected: ['the cable was pulled out', 'вынули кабель'],
    PowerLoss: ['the station lost power', 'станция потеряла питание'],
    NoError: ['no fault', 'неисправностей нет'],
  };
  App.valueWords = function (value) {
    var row = VALUE[value];
    return row ? App.t(row[0], row[1]) : '';
  };

  // ---- one message, taken apart ------------------------------------------------------------------

  var KIND = { 2: ['a question', 'вопрос'], 3: ['an answer', 'ответ'], 4: ['a refusal', 'отказ'] };

  /* Parse one frame. Returns {kind, id, action, payload, bad} */
  App.readFrame = function (text) {
    var body;
    try {
      body = JSON.parse(text);
    } catch (e) {
      return { bad: true, raw: text };
    }
    if (!Array.isArray(body) || !body.length) return { bad: true, raw: text };
    var kind = body[0];
    if (kind === 2) return { kind: 2, id: body[1], action: body[2], payload: body[3] || {} };
    if (kind === 3) return { kind: 3, id: body[1], payload: body[2] || {} };
    if (kind === 4) return { kind: 4, id: body[1], code: body[2], description: body[3], payload: body[4] || {} };
    return { bad: true, raw: text };
  };

  App.kindWords = function (kind) {
    var row = KIND[kind];
    return row ? App.t(row[0], row[1]) : '';
  };

  /* The raw JSON with its parts coloured. */
  App.renderWire = function (frame, text) {
    var box = App.h('div', { class: 'wire' });
    if (frame.bad) {
      box.appendChild(document.createTextNode(text));
      return box;
    }
    box.appendChild(document.createTextNode('[ '));
    box.appendChild(App.h('i', { class: 'kind' }, String(frame.kind)));
    box.appendChild(document.createTextNode(', '));
    box.appendChild(App.h('i', { class: 'mid' }, JSON.stringify(frame.id)));
    if (frame.action) {
      box.appendChild(document.createTextNode(', '));
      box.appendChild(App.h('i', { class: 'act' }, JSON.stringify(frame.action)));
    }
    if (frame.kind === 4) {
      box.appendChild(document.createTextNode(', '));
      box.appendChild(App.h('i', { class: 'act' }, JSON.stringify(frame.code)));
      box.appendChild(document.createTextNode(', ' + JSON.stringify(frame.description)));
    }
    box.appendChild(document.createTextNode(', '));
    box.appendChild(App.h('i', { class: 'body' }, JSON.stringify(frame.payload)));
    box.appendChild(document.createTextNode(' ]'));
    return box;
  };

  /* Flatten a payload into rows of name, value and a plain explanation. */
  App.flatten = function (value, prefix, rows, depth) {
    rows = rows || [];
    depth = depth || 0;
    prefix = prefix || '';
    if (value === null || typeof value !== 'object') {
      rows.push({ key: prefix, value: value });
      return rows;
    }
    if (Array.isArray(value)) {
      if (!value.length) rows.push({ key: prefix, value: '[]' });
      value.forEach(function (item, index) {
        App.flatten(item, prefix + '[' + index + ']', rows, depth + 1);
      });
      return rows;
    }
    Object.keys(value).forEach(function (key) {
      App.flatten(value[key], prefix ? prefix + '.' + key : key, rows, depth + 1);
    });
    return rows;
  };

  App.renderParts = function (payload) {
    var box = App.h('div', { class: 'parts' });
    var rows = App.flatten(payload);
    if (!rows.length) {
      box.appendChild(App.h('div', { class: 'part' }, App.h('div', { class: 'd' }, App.both('nothing to say: an empty answer', 'сказать нечего: пустой ответ'))));
      return box;
    }
    rows.forEach(function (row) {
      var leaf = row.key.split('.').pop().replace(/\[\d+\]/, '');
      var words = App.fieldWords(leaf);
      var meaning = typeof row.value === 'string' ? App.valueWords(row.value) : '';
      box.appendChild(
        App.h('div', { class: 'part' }, [
          App.h('div', { class: 'k' }, row.key),
          App.h('div', { class: 'v' }, JSON.stringify(row.value)),
          words || meaning ? App.h('div', { class: 'd' }, [words, meaning ? ' — ' + meaning : ''].join('')) : null,
        ])
      );
    });
    return box;
  };

  /* One sentence for a whole message. */
  App.sayFrame = function (frame) {
    if (frame.bad) return App.t('This is not an OCPP message at all.', 'Это вообще не сообщение OCPP.');
    if (frame.kind === 4) {
      return App.t('Refused: ', 'Отказ: ') + (frame.description || frame.code);
    }
    if (frame.kind === 3) {
      var status = frame.payload && (frame.payload.status || (frame.payload.idTagInfo || {}).status || (frame.payload.idTokenInfo || {}).status);
      if (status) return App.t('The answer: ', 'Ответ: ') + (App.valueWords(status) || status);
      if (frame.payload && frame.payload.currentTime) return App.t('Noted, and here is the time.', 'Принято, и вот время.');
      if (!frame.payload || !Object.keys(frame.payload).length) return App.t('Noted, nothing to add.', 'Принято, добавить нечего.');
      return App.t('The answer.', 'Ответ.');
    }
    return App.actionWords(frame.action) || frame.action;
  };

  // ---- test titles in the current language --------------------------------------------------------

  var RU = window.RU_DATA || { tests: {}, steps: [], mutants: {}, suites: {}, areas: {} };
  var stepPatterns = RU.steps.map(function (pair) {
    return [new RegExp('^' + pair[0] + '$'), pair[1]];
  });
  App.testTitle = function (test) {
    return App.lang === 'ru' && RU.tests[test.name] ? RU.tests[test.name] : test.title;
  };
  App.stepText = function (text) {
    if (App.lang !== 'ru') return text;
    for (var i = 0; i < stepPatterns.length; i++) {
      if (stepPatterns[i][0].test(text)) return text.replace(stepPatterns[i][0], stepPatterns[i][1]);
    }
    return text;
  };
  App.suiteName = function (suite) {
    var row = RU.suites[suite];
    return row ? App.t(row[0], row[1]) : suite;
  };
  App.suiteBlurb = function (suite) {
    var row = RU.suites[suite];
    return row ? App.t(row[2], row[3]) : '';
  };
  App.mutantTitle = function (mutant) {
    return App.lang === 'ru' && RU.mutants[mutant.id] ? RU.mutants[mutant.id] : mutant.title;
  };
  App.areaName = function (area) {
    var row = (RU.areas || {})[area];
    return row ? App.t(row[0], row[1]) : area;
  };
  App.RU = RU;
  void Twin;
})();
