/* Рендер ответов агента: план, инфо, отказ. Без зависимостей, кроме Leaflet для карты. */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ico = (id, cls='') => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;

const PLANS = {};
function renderPlan(p, mapId) {
  PLANS[mapId] = p;
  let n = 0;
  const days = p.days.map((d, di) => `
    <div class="day">
      <div class="day-h" role="button" tabindex="0" data-map="${mapId}" data-day="${di}" title="Показать маршрут этого дня на карте"><b>${esc(d.label)}</b><span>${esc(d.date)}</span><em>${ico('route')}Маршрут дня</em></div>
      ${d.stops.map(s => `
        <div class="stop"><span class="t">${esc(s.time)}</span><span class="n">${++n}</span>
          <div><b>${esc(s.name)}</b><small>${esc(s.note)}</small>
          ${s.status ? `<span class="badge">${esc(s.status)}</span>` : ''}</div></div>`).join('')}
    </div>`).join('');
  const wx = p.weather.map(w => `<div>${ico(w.icon)}<p><b>${esc(w.day)} · ${esc(w.temp)}</b><span>${esc(w.desc)}</span></p></div>`).join('');
  const sleep = p.overnight ? `<div class="sleep">${ico('bed')}<div><b>Ночёвка: ${esc(p.overnight.name)}</b>${esc(p.overnight.note)}</div><em>${esc(p.overnight.price)}</em></div>` : '';
  const notes = `<div class="notes"><h3>Что учесть</h3>${p.notes.map(([k, t]) =>
    `<div class="note ${k}">${ico(k === 'ok' ? 'check' : 'warn')}<span>${esc(t)}</span></div>`).join('')}</div>`;
  return `
  <article class="plan">
    <div class="plan-h"><h2>${esc(p.title)}</h2><p>${esc(p.summary)}</p>
      <div class="chips">${p.intents.map(i => `<span class="chip">${esc(i)}</span>`).join('')}</div></div>
    <div class="wx">${wx}</div>
    <button class="map-open-btn" data-map="${mapId}">${ico('route')}<div><b>Маршрут на карте</b>${n} точек · ${p.days.length} дн.</div><em>Открыть карту</em></button>
    <div class="days">${days}</div>
    ${sleep}${notes}
    <div class="checks">${p.checks.map(c => `<span>${ico('check')}${esc(c)}</span>`).join('')}</div>
    <div class="meta">Тулов вызвано: ${p.meta.tools} · ${p.meta.seconds} с · ${p.meta.tokens.toLocaleString('ru')} токенов</div>
  </article>`;
}

/* Мини-markdown для ответов модели. Весь текст СНАЧАЛА экранируется, потом добавляются только наши теги:
   **жирный**, списки («- », «* », «1. ») и ссылки https://… (target=_blank + noopener). HTML из ответа не выполняется никогда. */
function mdInline(t) {
  return esc(t)
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]\n]+)\]\((https:\/\/[^\s)<>"']+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https:\/\/[^\s<>"'()]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
}
function renderMd(text, mapId) {
  const out = []; let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(text ?? '').split('\n')) {
    const line = raw.trim();
    const ul = line.match(/^[-*•]\s+(.*)$/), ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const kind = ul ? 'ul' : 'ol';
      if (list !== kind) { close(); out.push(`<${kind} class="md">`); list = kind; }
      out.push(`<li>${mdInline((ul || ol)[1])}</li>`);
    } else if (!line) { close(); }
    else {
      close();
      const plain = line.replace(/^#{1,4}\s+/, '').replace(/[*_`━]/g, ' ').replace(/\s+/g, ' ').trim();
      const dm = mapId && MAPS[mapId] && /^(\*\*|#{1,4}\s)/.test(line) && plain.match(/^День\s*(\d+)/i);
      if (dm) out.push(`<h4 class="md-h"><button type="button" class="day-go" data-mapdata="${esc(mapId)}" data-day="${dm[1]}"><span>${esc(plain)}</span><em>${ico('route')}Маршрут дня</em></button></h4>`);   // клик — на карте только этот день
      else if (/^план поездки\W*$/i.test(plain)) out.push('<div class="plan-start"><span>План поездки</span></div>');   // начало плана видно сразу
      else if (/^(\*\*|#{1,4}\s)[^*]+(\*\*)?$/.test(line)) out.push(`<h4 class="md-h">${esc(plain)}</h4>`);       // заголовки блоков и дней
      else out.push(`<p>${mdInline(line.replace(/^#{1,4}\s+/, ''))}</p>`);
    }
  }
  close();
  return out.join('');
}

const MAPS = {};
function hasMap(m) { return !!m && ((m.markers && m.markers.length) || (m.routes && m.routes.length) || m.route); }
function renderInfo(r, mapId) {
  let mapBtn = '';
  if (mapId && hasMap(r.map)) {
    MAPS[mapId] = r.map;
    const n = (r.map.markers || []).length, rt = (r.map.routes || []).length;
    const sub = [n ? `${n} ${n === 1 ? 'точка' : (n < 5 ? 'точки' : 'точек')}` : '', rt ? `маршрутов: ${rt}` : ''].filter(Boolean).join(' · ');
    mapBtn = `<button class="map-open-btn" data-mapdata="${esc(mapId)}">${ico('route')}<div><b>Показать на карте</b>${esc(sub)}</div><em>Открыть карту</em></button>`;
  }
  const bullets = (r.bullets || []).length ? `<ul>${r.bullets.map(b => `<li>${esc(b)}</li>`).join('')}</ul>` : '';
  const fu = r.followup ? `<p class="fu">${esc(r.followup)}</p>` : '';
  return `<div class="bubble"><h3>${esc(r.title)}</h3>${renderMd(r.text, mapId)}${bullets}${fu}${mapBtn}</div>`;
}

function renderRefusal(r) {
  return `<div class="bubble refuse"><h3>${ico('ban')}${esc(r.title)}</h3><p>${esc(r.text)}</p>
    <div class="sugg">
      <button data-q="Куда съездить на выходные по Приморскому краю на машине?">Куда съездить на выходные?</button>
      <button data-q="Как добраться до острова Попова на катере?">Катер на остров Попова</button>
    </div></div>`;
}

function renderAnswer(r, mapId) {
  return r.type === 'plan' ? renderPlan(r, mapId) : r.type === 'info' ? renderInfo(r, mapId) : renderRefusal(r);
}

