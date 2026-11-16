/* The picture: a car, the cable, the charge point, and the back office at the other end of a network. */
(function () {
  'use strict';
  var App = window.App;
  var Twin = window.Twin;
  var NS = 'http://www.w3.org/2000/svg';
  var count = 0;

  function both(x, y, en, ru, cls, anchor) {
    var a = anchor ? ' text-anchor="' + anchor + '"' : '';
    return (
      '<text class="en ' + (cls || '') + '" x="' + x + '" y="' + y + '"' + a + '>' + en + '</text>' +
      '<text class="ru ' + (cls || '') + '" x="' + x + '" y="' + y + '"' + a + '>' + ru + '</text>'
    );
  }

  function markup(id) {
    return (
      '<svg xmlns="' + NS + '" viewBox="0 0 760 300" role="img" aria-labelledby="' + id + '-t">' +
      '<title id="' + id + '-t">An electric car on a cable to a charge point, and the charge point on a network to its back office</title>' +
      '<rect width="760" height="300" fill="var(--sky-day)"/>' +
      '<rect y="236" width="760" height="64" fill="var(--sunk)"/>' +
      '<line x1="0" y1="236" x2="760" y2="236" stroke="var(--line)"/>' +
      // the car
      '<g class="car">' +
      '<path d="M40 214 L52 176 Q56 166 68 166 L166 166 Q178 166 184 176 L198 214 Z" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.5"/>' +
      '<path d="M66 206 L76 180 Q78 176 84 176 L150 176 Q156 176 158 180 L168 206 Z" fill="var(--sky-night)" opacity="0.25"/>' +
      '<rect x="34" y="210" width="172" height="20" rx="8" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.5"/>' +
      '<circle cx="70" cy="232" r="13" fill="var(--ink)"/><circle cx="170" cy="232" r="13" fill="var(--ink)"/>' +
      '<rect class="battery" x="96" y="214" width="48" height="12" rx="3" fill="var(--sunk)" stroke="var(--ink)"/>' +
      '<rect class="batteryfill" x="98" y="216" width="0" height="8" rx="2" fill="var(--sun-fill)"/>' +
      '</g>' +
      both(120, 262, 'the car', 'машина', 'lbl', 'middle') +
      // the cable from the station to the car
      '<path class="plug" d="M300 196 Q 260 196 240 206 Q 222 214 206 214"/>' +
      '<path class="flow power" d="M296 192 L 212 210"/>' +
      '<text class="lbl pilottag" x="250" y="178" text-anchor="middle">A</text>' +
      // the charge point
      '<rect x="300" y="120" width="86" height="116" rx="8" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.5"/>' +
      '<rect x="312" y="134" width="62" height="34" rx="4" fill="var(--sunk)"/>' +
      '<text class="lcd screen1" x="343" y="150" text-anchor="middle" style="font-size:12px">—</text>' +
      '<text class="lcd screen2" x="343" y="163" text-anchor="middle" style="font-size:10px" fill="var(--muted)"></text>' +
      '<circle class="led" cx="314" cy="182" r="5"/>' +
      '<rect x="326" y="176" width="48" height="12" rx="3" fill="var(--sunk)"/>' +
      '<rect class="meterfill" x="327" y="177" width="0" height="10" rx="2" fill="var(--sun-fill)"/>' +
      '<rect x="318" y="198" width="50" height="26" rx="4" fill="var(--sunk)" stroke="var(--line)"/>' +
      '<circle class="reader" cx="343" cy="211" r="7" fill="none" stroke="var(--muted)" stroke-width="2"/>' +
      both(343, 262, 'the charge point', 'зарядная станция', 'lbl', 'middle') +
      // the network to the back office
      '<path class="flow data" d="M392 160 L 596 160"/>' +
      '<g class="cut" stroke="var(--bad)" stroke-width="4" stroke-linecap="round" opacity="0">' +
      '<path d="M482 146 L 506 170 M 506 146 L 482 170"/></g>' +
      both(494, 142, 'questions and answers', 'вопросы и ответы', 'lbl', 'middle') +
      // the back office
      '<rect x="600" y="118" width="120" height="84" rx="8" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.5"/>' +
      '<rect x="612" y="130" width="96" height="10" rx="3" fill="var(--sunk)"/>' +
      '<rect x="612" y="146" width="96" height="10" rx="3" fill="var(--sunk)"/>' +
      '<rect x="612" y="162" width="96" height="10" rx="3" fill="var(--sunk)"/>' +
      '<circle class="backled" cx="702" cy="192" r="4" fill="var(--ok)"/>' +
      '<text class="lcd backtext" x="660" y="192" text-anchor="middle" style="font-size:10px" fill="var(--muted)"></text>' +
      both(660, 224, 'the back office', 'облако оператора', 'lbl', 'middle') +
      '<text class="lcd clk" x="16" y="28" style="font-size:15px"></text>' +
      '</svg>'
    );
  }

  App.Scene = function (container) {
    count += 1;
    container.innerHTML = markup('scene' + count);
    var svg = container.querySelector('svg');
    function q(selector) {
      return svg.querySelector(selector);
    }
    var parts = {
      power: q('.flow.power'),
      data: q('.flow.data'),
      cut: q('.cut'),
      led: q('.led'),
      screen1: q('.screen1'),
      screen2: q('.screen2'),
      clk: q('.clk'),
      pilot: q('.pilottag'),
      plug: q('.plug'),
      meterfill: q('.meterfill'),
      batteryfill: q('.batteryfill'),
      backled: q('.backled'),
      backtext: q('.backtext'),
      reader: q('.reader'),
    };

    function flow(node, fraction) {
      if (fraction > 0.002) {
        node.classList.add('on');
        node.style.setProperty('--flow-s', (2.0 - 1.6 * Math.min(1, fraction)).toFixed(2) + 's');
      } else {
        node.classList.remove('on');
      }
    }

    return {
      /* s: {clock, pilot, status, amps, maxA, online, talking, sessions, energyWh, charged} */
      update: function (s) {
        var St = Twin.Status;
        flow(parts.power, s.amps / Twin.EVSE_MAX_A);
        if (s.talking && s.online) parts.data.classList.add('on');
        else parts.data.classList.remove('on');
        parts.data.style.setProperty('--flow-s', '0.7s');
        parts.cut.setAttribute('opacity', s.online ? '0' : '1');
        parts.backled.setAttribute('fill', s.online ? 'var(--ok)' : 'var(--bad)');
        parts.backtext.textContent = s.sessions === undefined ? '' : App.t('sessions: ', 'сессий: ') + s.sessions;

        var colour =
          s.status === St.CHARGING
            ? 'var(--ok)'
            : s.status === St.FAULTED
              ? 'var(--bad)'
              : s.status === St.AVAILABLE
                ? 'var(--idle)'
                : 'var(--sun-fill)';
        parts.led.setAttribute('fill', colour);
        parts.reader.setAttribute('stroke', s.status === Twin.Status.PREPARING ? 'var(--sun)' : 'var(--muted)');
        parts.screen1.textContent = s.amps > 0 ? App.num(Math.round(s.amps * Twin.NOMINAL_V)) + ' ' + App.t('W', 'Вт') : '—';
        parts.screen2.textContent = s.short || s.status || '';
        parts.pilot.textContent = s.pilot;
        parts.pilot.setAttribute('fill', s.pilot === 'C' ? 'var(--sun)' : s.pilot === 'E' ? 'var(--bad)' : 'var(--muted)');
        parts.plug.setAttribute('stroke', s.pilot === 'A' ? 'var(--line)' : 'var(--roof)');
        parts.meterfill.setAttribute('width', (48 * Math.min(1, s.amps / Twin.EVSE_MAX_A)).toFixed(1));
        parts.batteryfill.setAttribute('width', (44 * Math.min(1, (s.charged || 0))).toFixed(1));
        parts.clk.textContent = App.clock(s.clock);
        parts.clk.setAttribute('fill', 'var(--ink)');
      },
    };
  };

  /* Plain words for a connector status. */
  App.statusWords = function (status) {
    return App.valueWords(status) || status || '';
  };
})();
