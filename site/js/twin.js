/* The JavaScript twin of src/evse_lab: the same charge point, written again for the browser.

   It repeats the rules of station.py, v16.py and v201.py in the same order, so the two build the same
   messages. site/test/twin.test.mjs replays conversations recorded from the Python station against
   this file and fails on the first message that differs.

   Optional planted bugs (the `bugs` object) mirror mutants of scripts/mutants.py by their ids. */
(function (root) {
  'use strict';

  var NOMINAL_V = 230.0;
  var EVSE_MAX_A = 32.0;
  var CABLE_MAX_A = 32.0;
  var MIN_CHARGE_A = 6.0;
  var METER_SAMPLE_S = 60;
  var START_ENERGY_WH = 4500000;
  var EPOCH = Date.UTC(2026, 5, 15, 0, 0, 0);

  var Pilot = { A: 'A', B: 'B', C: 'C', E: 'E' };
  var Status = {
    AVAILABLE: 'Available',
    PREPARING: 'Preparing',
    CHARGING: 'Charging',
    SUSPENDED_EVSE: 'SuspendedEVSE',
    SUSPENDED_EV: 'SuspendedEV',
    FINISHING: 'Finishing',
    RESERVED: 'Reserved',
    UNAVAILABLE: 'Unavailable',
    FAULTED: 'Faulted',
  };
  var Reason = {
    LOCAL: 'Local',
    REMOTE: 'Remote',
    EV_DISCONNECTED: 'EVDisconnected',
    POWER_LOSS: 'PowerLoss',
    DE_AUTHORIZED: 'DeAuthorized',
    OTHER: 'Other',
  };
  var Auth = { ACCEPTED: 'Accepted', BLOCKED: 'Blocked', EXPIRED: 'Expired', INVALID: 'Invalid', CONCURRENT: 'ConcurrentTx' };
  var KNOWN_TAGS = { 'CARD-OK': Auth.ACCEPTED, 'CARD-OK-2': Auth.ACCEPTED, 'CARD-BLOCKED': Auth.BLOCKED, 'CARD-EXPIRED': Auth.EXPIRED };

  function iso(clock) {
    return new Date(EPOCH + clock * 1000).toISOString().replace('.000Z', 'Z');
  }

  // ---- the connector ------------------------------------------------------------------------------

  function Connector(id) {
    this.id = id;
    this.pilot = Pilot.A;
    this.status = Status.AVAILABLE;
    this.operative = true;
    this.transaction = null;
    this.profiles = {};
    this.car_max_a = 16.0;
    this.locked = false;
  }
  Connector.prototype.limitA = function (now, bugs) {
    var allowed = Math.min(EVSE_MAX_A, CABLE_MAX_A);
    var active = [];
    for (var key in this.profiles) {
      var p = this.profiles[key];
      if (profileActive(p, now, bugs)) active.push(p);
    }
    if (active.length) {
      var levels = active.map(function (p) {
        return p.stack_level;
      });
      var top = bugs.M29 ? Math.min.apply(null, levels) : Math.max.apply(null, levels);
      var same = active
        .filter(function (p) {
          return p.stack_level === top;
        })
        .map(function (p) {
          return p.limit_a;
        });
      var pick = bugs.M28 ? Math.max.apply(null, same) : Math.min.apply(null, same);
      allowed = Math.min(allowed, pick);
    }
    return allowed;
  };
  Connector.prototype.offeredA = function (now, bugs) {
    var limit = this.limitA(now, bugs);
    if (bugs.M32) return limit;
    return limit < MIN_CHARGE_A ? 0.0 : limit;
  };
  Connector.prototype.drawnA = function (now, bugs) {
    if (this.pilot !== Pilot.C || this.transaction === null || this.transaction.stopped_at !== null) return 0.0;
    if (bugs.M33) return this.car_max_a;
    return Math.min(this.offeredA(now, bugs), this.car_max_a);
  };

  function profileActive(p, now, bugs) {
    if (!bugs.M31 && p.valid_from !== null && now < p.valid_from) return false;
    if (bugs.M30) return true;
    return !(p.valid_to !== null && now >= p.valid_to);
  }

  // ---- the station -------------------------------------------------------------------------------

  function Station(connectors, bugs) {
    this.bugs = bugs || {};
    this.serial = 'SIM-EVSE-0001';
    this.vendor = 'QA Portfolio Lab';
    this.model = 'SIM-AC-22';
    this.firmware = '1.0.0';
    this.reset(connectors || 2);
  }

  Station.prototype.reset = function (count) {
    this.clock = 8 * 3600;
    this.connectors = {};
    var n = count || Object.keys(this.connectors).length || 2;
    for (var i = 1; i <= n; i++) this.connectors[i] = new Connector(i);
    this.meter_wh = START_ENERGY_WH;
    this.booted = false;
    this.boot_interval = 300;
    this.online = true;
    this.events = [];
    this.queue = [];
    this.next_local_id = 1;
    this.last_heartbeat = 0;
    this.last_sample = {};
    this.auth_cache = {};
    this.reset_pending = null;
  };

  Station.prototype.connector = function (n) {
    var c = this.connectors[n];
    if (!c) throw new Error('no connector ' + n);
    return c;
  };

  Station.prototype._report = function (event) {
    if (this.online || this.bugs.M20) {
      this.events.push(event);
    } else if (!this.bugs.M17) {
      event.offline = true;
      this.queue.push(event);
    }
  };

  Station.prototype.takeEvents = function () {
    var out = this.events;
    this.events = [];
    return out;
  };

  Station.prototype.goOffline = function () {
    this.online = false;
  };
  Station.prototype.goOnline = function () {
    this.online = true;
    var kept = this.bugs.M18 ? this.queue.slice().reverse() : this.queue;
    this.events = this.events.concat(kept);
    this.queue = [];
  };

  Station.prototype.boot = function () {
    this.last_heartbeat = this.clock;
    this._report({ kind: 'boot', at: this.clock });
  };
  Station.prototype.acceptBoot = function (interval) {
    this.booted = true;
    this.boot_interval = Math.max(1, interval);
    for (var n in this.connectors) this._report({ kind: 'status', connector: Number(n), at: this.clock, status: this.connectors[n].status });
  };

  Station.prototype.plugIn = function (n) {
    var c = this.connector(n || 1);
    c.pilot = Pilot.B;
    c.locked = true;
    this._settle(c);
  };
  Station.prototype.carReady = function (n, ready) {
    var c = this.connector(n || 1);
    if (c.pilot !== Pilot.A) c.pilot = ready === false ? Pilot.B : Pilot.C;
    this._settle(c);
  };
  Station.prototype.unplug = function (n) {
    var c = this.connector(n || 1);
    if (c.transaction !== null && c.transaction.stopped_at === null && !this.bugs.M21) {
      this.stopTransaction(c.id, Reason.EV_DISCONNECTED);
    }
    c.pilot = Pilot.A;
    c.locked = false;
    this._settle(c);
  };

  Station.prototype.authorize = function (idTag) {
    if (this.online) {
      var status = KNOWN_TAGS[idTag] || Auth.INVALID;
      this.auth_cache[idTag] = status;
      this._report({ kind: 'authorize', at: this.clock, id_tag: idTag });
      return status;
    }
    if (this.bugs.M12) return Auth.ACCEPTED;
    return this.auth_cache[idTag] || Auth.INVALID;
  };

  Station.prototype.presentCard = function (idTag, n) {
    var c = this.connector(n || 1);
    if (c.transaction !== null && c.transaction.stopped_at === null) {
      if (this.bugs.M11 || c.transaction.id_tag === idTag) {
        this.stopTransaction(c.id, Reason.LOCAL);
        return Auth.ACCEPTED;
      }
      return Auth.CONCURRENT;
    }
    var status = this.authorize(idTag);
    if (status === Auth.ACCEPTED || this.bugs.M10) this.startTransaction(idTag, c.id);
    return status;
  };

  Station.prototype.startTransaction = function (idTag, n) {
    var c = this.connector(n || 1);
    var transaction = {
      local_id: this.next_local_id++,
      connector: c.id,
      id_tag: idTag,
      meter_start: this.bugs.M14 ? 0 : Math.trunc(this.meter_wh),
      started_at: this.clock,
      remote_id: null,
      meter_stop: null,
      stopped_at: null,
      reason: null,
      seq_no: 0,
    };
    c.transaction = transaction;
    this.last_sample[c.id] = this.clock;
    this._report({ kind: 'started', connector: c.id, at: this.clock, transaction: transaction, id_tag: idTag });
    this._settle(c);
    return transaction;
  };

  Station.prototype.stopTransaction = function (n, reason) {
    var c = this.connector(n || 1);
    var transaction = c.transaction;
    if (transaction === null || transaction.stopped_at !== null) return null;
    transaction.meter_stop = this.bugs.M13 ? transaction.meter_start : Math.trunc(this.meter_wh);
    transaction.stopped_at = this.clock;
    transaction.reason = reason || Reason.LOCAL;
    this._report({ kind: 'stopped', connector: c.id, at: this.clock, transaction: transaction, reason: transaction.reason });
    this._settle(c);
    return transaction;
  };

  Station.prototype.setProfile = function (n, profile) {
    var c = this.connector(n);
    c.profiles[profile.id] = profile;
    this._settle(c);
  };
  Station.prototype.clearProfiles = function (n, profileId) {
    var removed = 0;
    for (var key in this.connectors) {
      var c = this.connectors[key];
      if (n !== null && n !== undefined && Number(key) !== n) continue;
      for (var id in c.profiles) {
        if (profileId === null || profileId === undefined || Number(id) === profileId) {
          delete c.profiles[id];
          removed += 1;
        }
      }
      this._settle(c);
    }
    return removed;
  };
  Station.prototype.setOperative = function (n, operative) {
    if (n === 0) {
      for (var key in this.connectors) this.setOperative(Number(key), operative);
      return;
    }
    var c = this.connector(n);
    c.operative = operative;
    this._settle(c);
  };
  Station.prototype.unlock = function (n) {
    var c = this.connector(n);
    if (!this.bugs.M25 && c.transaction !== null && c.transaction.stopped_at === null) return 'UnlockFailed';
    if (!c.locked) return c.pilot === Pilot.A ? 'NotSupported' : 'Unlocked';
    c.locked = false;
    return 'Unlocked';
  };

  Station.prototype.advance = function (seconds) {
    for (var i = 0; i < seconds; i++) this._tick();
  };

  Station.prototype._tick = function () {
    this.clock += 1;
    for (var key in this.connectors) {
      var c = this.connectors[key];
      var drawn = c.drawnA(this.clock, this.bugs);
      if (this.bugs.M15) {
        this.meter_wh += (NOMINAL_V * c.offeredA(this.clock, this.bugs)) / 3600.0;
      } else if (drawn > 0) {
        this.meter_wh += (NOMINAL_V * drawn) / 3600.0;
      }
      var transaction = c.transaction;
      if (transaction !== null && transaction.stopped_at === null) {
        var last = this.last_sample[c.id] === undefined ? this.clock : this.last_sample[c.id];
        var due = this.bugs.M26 ? last : last + METER_SAMPLE_S;
        if (this.clock >= due) {
          this.last_sample[c.id] = this.clock;
          this._report({ kind: 'sample', connector: c.id, at: this.clock, transaction: transaction, meter_wh: Math.trunc(this.meter_wh) });
        }
      }
      this._settle(c);
    }
    var gap = this.bugs.M27 ? 1 : this.boot_interval;
    if (this.booted && this.clock - this.last_heartbeat >= gap) {
      this.last_heartbeat = this.clock;
      this._report({ kind: 'heartbeat', at: this.clock });
    }
  };

  Station.prototype._settle = function (c) {
    var status = this._statusOf(c);
    if (status !== c.status) {
      c.status = status;
      this._report({ kind: 'status', connector: c.id, at: this.clock, status: status });
    }
  };

  Station.prototype._statusOf = function (c) {
    var running = c.transaction !== null && c.transaction.stopped_at === null;
    if (c.pilot === Pilot.E && !this.bugs.M24) return Status.FAULTED;
    if (!c.operative && !running) return Status.UNAVAILABLE;
    if (running) {
      if (c.pilot !== Pilot.C && !this.bugs.M22) return Status.SUSPENDED_EV;
      if (c.offeredA(this.clock, this.bugs) <= 0 && !this.bugs.M23) return Status.SUSPENDED_EVSE;
      return Status.CHARGING;
    }
    if (c.pilot === Pilot.A) return Status.AVAILABLE;
    return c.transaction !== null ? Status.FINISHING : Status.PREPARING;
  };

  Station.prototype.powerW = function (n) {
    return NOMINAL_V * this.connector(n).drawnA(this.clock, this.bugs);
  };

  // ---- meter readings, in the shape each version wants ---------------------------------------------

  var MEASURANDS = [
    ['Energy.Active.Import.Register', 'Wh'],
    ['Power.Active.Import', 'W'],
    ['Current.Import', 'A'],
  ];

  function round1(x) {
    return Math.round(x * 10) / 10;
  }

  function sampledValue(version, wh, watts, amps, bugs) {
    var numbers = [wh, round1(watts), round1(amps)];
    var out = [];
    for (var i = 0; i < MEASURANDS.length; i++) {
      var measurand = MEASURANDS[i][0];
      var unit = MEASURANDS[i][1];
      if (version === '1.6' || (bugs && bugs.M39)) {
        // 1.6 carries the number as text, and Python writes a rounded float with one decimal: the
        // energy counter is whole, the power and the current always show one place.
        var text = i === 0 ? String(numbers[i]) : numbers[i].toFixed(1);
        out.push({ value: text, measurand: measurand, unit: unit, context: 'Sample.Periodic' });
      } else {
        out.push({ value: numbers[i], measurand: measurand, context: 'Sample.Periodic', unitOfMeasure: { unit: unit } });
      }
    }
    return out;
  }

  // ---- OCPP 1.6 -----------------------------------------------------------------------------------

  function V16(bugs) {
    this.version = '1.6';
    this.bugs = bugs || {};
    this.next = 1;
  }
  V16.prototype._call = function (action, payload) {
    var id = ('0000000' + this.next++).slice(-8);
    return [2, id, action, payload];
  };

  V16.prototype.callsFor = function (station, event) {
    var c, amps, transaction;
    if (event.kind === 'boot') {
      return [
        this._call('BootNotification', {
          chargePointVendor: station.vendor,
          chargePointModel: station.model,
          chargePointSerialNumber: station.serial,
          firmwareVersion: station.firmware,
        }),
      ];
    }
    if (event.kind === 'status') {
      return [
        this._call('StatusNotification', {
          connectorId: event.connector,
          errorCode: event.status === Status.FAULTED ? 'OtherError' : 'NoError',
          status: event.status,
          timestamp: iso(event.at),
        }),
      ];
    }
    if (event.kind === 'authorize') return [this._call('Authorize', { idTag: event.id_tag })];
    if (event.kind === 'started') {
      transaction = event.transaction;
      return [
        this._call('StartTransaction', {
          connectorId: transaction.connector,
          idTag: transaction.id_tag,
          meterStart: transaction.meter_start,
          timestamp: iso(transaction.started_at),
        }),
      ];
    }
    if (event.kind === 'sample') {
      transaction = event.transaction;
      c = station.connector(event.connector);
      amps = c.drawnA(event.at, station.bugs);
      var payload = {
        connectorId: event.connector,
        meterValue: [{ timestamp: iso(event.at), sampledValue: sampledValue('1.6', event.meter_wh, NOMINAL_V * amps, amps, this.bugs) }],
      };
      if (transaction.remote_id !== null) payload.transactionId = transaction.remote_id;
      return [this._call('MeterValues', payload)];
    }
    if (event.kind === 'stopped') {
      transaction = event.transaction;
      var number = this.bugs.M34
        ? transaction.local_id
        : transaction.remote_id !== null
          ? transaction.remote_id
          : -transaction.local_id;
      return [
        this._call('StopTransaction', {
          meterStop: transaction.meter_stop,
          timestamp: iso(event.at),
          transactionId: number,
          reason: event.reason || Reason.LOCAL,
          idTag: transaction.id_tag,
        }),
      ];
    }
    if (event.kind === 'heartbeat') return [this._call('Heartbeat', {})];
    throw new Error('no OCPP 1.6 message for ' + event.kind);
  };

  V16.prototype.onResult = function (station, call, payload) {
    if (call[2] === 'BootNotification') {
      if (this.bugs.M35 || payload.status === 'Accepted') station.acceptBoot(payload.interval || 300);
    } else if (call[2] === 'StartTransaction') {
      for (var key in station.connectors) {
        var transaction = station.connectors[key].transaction;
        if (transaction !== null && transaction.remote_id === null && transaction.id_tag === call[3].idTag) {
          transaction.remote_id = payload.transactionId;
          break;
        }
      }
    }
  };

  // ---- OCPP 2.0.1 ----------------------------------------------------------------------------------

  function V201(bugs) {
    this.version = '2.0.1';
    this.bugs = bugs || {};
    this.next = 1;
    this.reported = {};
  }
  V201.prototype._call = V16.prototype._call;

  function statusName(status) {
    var occupied = [Status.PREPARING, Status.CHARGING, Status.SUSPENDED_EV, Status.SUSPENDED_EVSE, Status.FINISHING];
    return occupied.indexOf(status) >= 0 ? 'Occupied' : status;
  }
  function idToken(tag) {
    return { idToken: tag, type: 'ISO14443' };
  }
  function transactionId(localId, bugs) {
    var base = 'TX-' + ('00000' + localId).slice(-6);
    return bugs && bugs.M37 ? base + '-' + ((Date.now() % 1000) + localId) : base;
  }
  function endTrigger(reason) {
    if (reason === Reason.EV_DISCONNECTED) return 'EVCommunicationLost';
    if (reason === Reason.REMOTE) return 'RemoteStop';
    return 'StopAuthorized';
  }

  V201.prototype.callsFor = function (station, event) {
    if (event.kind === 'boot') {
      return [
        this._call('BootNotification', {
          reason: 'PowerUp',
          chargingStation: { model: station.model, vendorName: station.vendor, serialNumber: station.serial, firmwareVersion: station.firmware },
        }),
      ];
    }
    if (event.kind === 'status') {
      var name = statusName(event.status);
      if (!this.bugs.M40 && !event.forced && this.reported[event.connector] === name) return [];
      this.reported[event.connector] = name;
      return [this._call('StatusNotification', { timestamp: iso(event.at), connectorStatus: name, evseId: event.connector, connectorId: 1 })];
    }
    if (event.kind === 'authorize') return [this._call('Authorize', { idToken: idToken(event.id_tag) })];
    if (event.kind === 'started' || event.kind === 'sample' || event.kind === 'stopped') {
      return [this._transactionEvent(station, event)];
    }
    if (event.kind === 'heartbeat') return [this._call('Heartbeat', {})];
    throw new Error('no OCPP 2.0.1 message for ' + event.kind);
  };

  V201.prototype._transactionEvent = function (station, event) {
    var transaction = event.transaction;
    var c = station.connector(event.connector);
    var amps = c.drawnA(event.at, station.bugs);
    var info = { transactionId: transactionId(transaction.local_id, this.bugs) };
    var kind, trigger, meterWh;
    if (event.kind === 'started') {
      kind = 'Started';
      trigger = 'Authorized';
      meterWh = transaction.meter_start;
      info.chargingState = 'EVConnected';
    } else if (event.kind === 'sample') {
      kind = 'Updated';
      trigger = 'MeterValuePeriodic';
      meterWh = event.meter_wh;
      info.chargingState = amps > 0 ? 'Charging' : 'SuspendedEVSE';
    } else {
      kind = 'Ended';
      trigger = endTrigger(event.reason);
      meterWh = transaction.meter_stop;
      info.chargingState = 'Idle';
      info.stoppedReason = event.reason || Reason.LOCAL;
    }
    var payload = {
      eventType: kind,
      timestamp: iso(event.at),
      triggerReason: trigger,
      seqNo: transaction.seq_no,
      transactionInfo: info,
      evse: { id: event.connector, connectorId: 1 },
      meterValue: [{ timestamp: iso(event.at), sampledValue: sampledValue('2.0.1', meterWh, NOMINAL_V * amps, amps, this.bugs) }],
    };
    if (event.kind === 'started') payload.idToken = idToken(transaction.id_tag);
    if (event.offline && !this.bugs.M38) payload.offline = true;
    if (!this.bugs.M36) transaction.seq_no += 1;
    return this._call('TransactionEvent', payload);
  };

  V201.prototype.onResult = function (station, call, payload) {
    if (call[2] === 'BootNotification' && payload.status === 'Accepted') station.acceptBoot(payload.interval || 300);
  };

  // ---- a small back office, enough to answer -------------------------------------------------------

  function clockOf(timestamp) {
    return Math.round((Date.parse(timestamp) - EPOCH) / 1000);
  }

  function Csms(version) {
    this.version = version;
    this.clock = 0; // the back office stamps its answers with the time it was last told
    this.sessions = {};
    this.received = [];
    this.statuses = [];
    this.boots = 0;
    this.heartbeats = 0;
    this.next = 1001;
  }
  Csms.prototype.handle = function (call) {
    var action = call[2];
    var payload = call[3];
    this.received.push(call);
    if (typeof payload.timestamp === 'string') this.clock = clockOf(payload.timestamp);
    if (action === 'BootNotification') {
      this.boots += 1;
      return { currentTime: iso(this.clock), interval: 300, status: 'Accepted' };
    }
    if (action === 'Heartbeat') {
      this.heartbeats += 1;
      return { currentTime: iso(this.clock) };
    }
    if (action === 'StatusNotification') {
      this.statuses.push(payload);
      return {};
    }
    if (action === 'Authorize') {
      var tag = this.version === '1.6' ? payload.idTag : payload.idToken.idToken;
      var status = KNOWN_TAGS[tag] || (this.version === '1.6' ? 'Invalid' : 'Unknown');
      return this.version === '1.6' ? { idTagInfo: { status: status } } : { idTokenInfo: { status: status } };
    }
    if (action === 'StartTransaction') {
      var number = this.next++;
      this.sessions[String(number)] = {
        key: String(number),
        id_tag: payload.idTag,
        meter_start: payload.meterStart,
        meter_stop: null,
        samples: [],
      };
      return { transactionId: number, idTagInfo: { status: KNOWN_TAGS[payload.idTag] || 'Invalid' } };
    }
    if (action === 'MeterValues') {
      var session = this.sessions[String(payload.transactionId)];
      if (session) session.samples.push(energyOf(payload));
      return {};
    }
    if (action === 'StopTransaction') {
      var ending = this.sessions[String(payload.transactionId)];
      if (ending) {
        ending.meter_stop = payload.meterStop;
        ending.reason = payload.reason;
      }
      return { idTagInfo: { status: 'Accepted' } };
    }
    if (action === 'TransactionEvent') {
      var key = payload.transactionInfo.transactionId;
      var energy = energyOf(payload);
      if (payload.eventType === 'Started') {
        this.sessions[key] = { key: key, id_tag: (payload.idToken || {}).idToken, meter_start: energy, meter_stop: null, samples: [] };
      }
      var row = this.sessions[key];
      if (row) {
        if (payload.eventType === 'Updated') row.samples.push(energy);
        if (payload.eventType === 'Ended') {
          row.meter_stop = energy;
          row.reason = payload.transactionInfo.stoppedReason;
        }
      }
      return payload.eventType === 'Started' && payload.idToken ? { idTokenInfo: { status: 'Accepted' } } : {};
    }
    return {};
  };

  function energyOf(payload) {
    var values = payload.meterValue || [];
    for (var i = 0; i < values.length; i++) {
      var samples = values[i].sampledValue || [];
      for (var k = 0; k < samples.length; k++) {
        if (samples[k].measurand === 'Energy.Active.Import.Register') return Math.round(Number(samples[k].value));
      }
    }
    return null;
  }

  // ---- the two ends joined -------------------------------------------------------------------------

  function Session(version, bugs) {
    this.bugs = bugs || {};
    this.version = version;
    this.station = new Station(2, this.bugs);
    this.dialect = version === '1.6' ? new V16(this.bugs) : new V201(this.bugs);
    this.csms = new Csms(version);
    this.waiting = [];
    this.pending = [];
    this.frames = []; // ['tx'|'rx', the JSON text]
  }

  Session.prototype.refresh = function () {
    this.waiting = this.waiting.concat(this.station.takeEvents());
    while (!this.pending.length && this.waiting.length) {
      this.pending = this.pending.concat(this.dialect.callsFor(this.station, this.waiting.shift()));
    }
  };

  /* Send one message and take the answer. Returns false when there is nothing to send. */
  Session.prototype.sendNext = function () {
    this.refresh();
    if (!this.pending.length) return false;
    var call = this.pending.shift();
    this.frames.push(['tx', JSON.stringify(call)]);
    var answer = [3, call[1], this.csms.handle(call)];
    this.frames.push(['rx', JSON.stringify(answer)]);
    this.dialect.onResult(this.station, call, answer[2]);
    return true;
  };

  Session.prototype.settle = function (rounds) {
    var guard = rounds || 600;
    while (guard-- > 0) {
      if (!this.sendNext()) return;
    }
  };

  Session.prototype.run = function (seconds, stepS) {
    var slice = stepS || seconds;
    var left = seconds;
    while (left > 0) {
      var now = Math.min(slice, left);
      this.station.advance(now);
      left -= now;
      this.settle();
    }
  };

  Session.prototype.sessionOf = function (idTag) {
    for (var key in this.csms.sessions) {
      if (this.csms.sessions[key].id_tag === idTag) return this.csms.sessions[key];
    }
    return null;
  };

  root.Twin = {
    Pilot: Pilot,
    Status: Status,
    Reason: Reason,
    Auth: Auth,
    KNOWN_TAGS: KNOWN_TAGS,
    NOMINAL_V: NOMINAL_V,
    MIN_CHARGE_A: MIN_CHARGE_A,
    EVSE_MAX_A: EVSE_MAX_A,
    METER_SAMPLE_S: METER_SAMPLE_S,
    START_ENERGY_WH: START_ENERGY_WH,
    Station: Station,
    Csms: Csms,
    Session: Session,
    V16: V16,
    V201: V201,
    iso: iso,
    clockOf: clockOf,
    statusName: statusName,
    sampledValue: sampledValue,
  };
})(typeof window !== 'undefined' ? window : globalThis);
