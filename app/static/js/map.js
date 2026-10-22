/* Карта справа: настоящий маршрут по дорогам (OSRM, routing.openstreetmap.de) + склейка совпадающих точек. */
const PROFILE = {car: 'routed-car', bus: 'routed-car', any: 'routed-car', bike: 'routed-bike', walk: 'routed-foot'};
// Цвет дня: дороги и маркеры одного дня одного цвета, следующий день — другого (цвета различимы между собой и на карте)
const DAY_COLORS = ['#0F52BA', '#E4572E', '#12907F', '#8E44AD', '#C9971C', '#D6336C', '#37474F'];
const dayColor = day => DAY_COLORS[(Math.max(1, day || 1) - 1) % DAY_COLORS.length];
const routeCache = new Map();
let lmap = null, lgroup = null, runId = 0;

const fmtDist = m => m >= 1000 ? (m / 1000).toFixed(m >= 100000 ? 0 : 1).replace('.', ',') + ' км' : Math.round(m) + ' м';
const fmtDur = s => { const h = Math.floor(s / 3600), m = Math.round(s % 3600 / 60); return h ? `${h} ч ${m} мин` : `${m} мин`; };
function hav(a, b) {
  const R = 6371, rad = x => x * Math.PI / 180, dLa = rad(b.lat - a.lat), dLo = rad(b.lng - a.lng);
  const h = Math.sin(dLa / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLo / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/* Совпадающие точки (< 300 м) объединяются: «1·6 Владивосток» */
function mergePoints(stops) {
  const groups = [];
  stops.forEach((s, i) => {
    const visit = {n: i + 1, time: s.time, day: s.dayLabel};
    const g = groups.find(g => hav(g, s) < 0.3);
    if (g) g.visits.push(visit); else groups.push({lat: s.lat, lng: s.lng, name: s.name, note: s.note, visits: [visit]});
  });
  return groups;
}

/* Дорога между двумя точками. Сначала через наш сервер (/api/route: у него цепочка из нескольких сервисов), если не вышло —
   напрямую из браузера. У каждого запроса есть таймаут: раньше зависший запрос не давал нарисовать вообще ничего. */
const MODE_OF = {'routed-car': 'car', 'routed-bike': 'bike', 'routed-foot': 'walk'};

function fetchTimeout(url, opts, ms) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
  return fetch(url, {...opts, signal: ctl.signal}).finally(() => clearTimeout(t));
}

async function legViaServer(a, b, profile) {
  const r = await fetchTimeout('/api/route', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin',
    body: JSON.stringify({points: [{lat: a.lat, lon: a.lng}, {lat: b.lat, lon: b.lng}], mode: MODE_OF[profile] || 'car'})
  }, 40000);
  if (!r.ok) throw new Error('server ' + r.status);
  const leg = ((await r.json()).legs || [])[0];
  if (!leg || !leg.ok) { const e = new Error(leg ? leg.reason : 'empty'); e.noRoad = leg && leg.reason === 'no_road'; throw e; }
  return {coords: leg.coords.map(([x, y]) => [y, x]), dist: leg.distance_km * 1000, dur: leg.duration_min * 60};
}

async function legViaBrowser(a, b, profile) {
  const xy = `${a.lng},${a.lat};${b.lng},${b.lat}`, q = '?overview=full&geometries=geojson';
  const routeQ = q + '&radiuses=unlimited;unlimited';
  const urls = [`https://routing.openstreetmap.de/${profile}/route/v1/driving/${xy}${routeQ}`];
  if (profile === 'routed-car') urls.push(`https://router.project-osrm.org/route/v1/driving/${xy}${routeQ}`);
  let err;
  for (const url of urls) {
    try {
      const j = await (await fetchTimeout(url, {}, 15000)).json();
      if (j.code !== 'Ok') { const e = new Error(j.code); e.noRoad = ['NoRoute', 'NoSegment'].includes(j.code); throw e; }
      const r = j.routes[0];
      return {coords: r.geometry.coordinates.map(([x, y]) => [y, x]), dist: r.distance, dur: r.duration};
    } catch (e) { err = e; console.warn('[route] браузер не достал до', url.split('/')[2], '-', e.message || e); }
  }
  throw err;
}

/* Подложка под линией дороги (белая обводка, как в 2ГИС / Яндекс Картах) */
function casing(pts) {
  // Обводка лежит в своём слое (pane) под слоем дорог. Раньше порядок задавался bringToBack(), который ничего не делает, пока у карты
  // нет первого вида: готовые линии с сервера рисуются раньше fitBounds, и белая обводка ложилась ПОВЕРХ цветной дороги.
  return L.polyline(pts, {pane: 'roadCasing', color: '#fff', weight: 9, opacity: .95, lineCap: 'round', lineJoin: 'round', interactive: false}).addTo(lgroup);
}

async function fetchLeg(a, b, profile) {
  const key = `${profile}|${a.lat.toFixed(5)},${a.lng.toFixed(5)}|${b.lat.toFixed(5)},${b.lng.toFixed(5)}`;
  if (routeCache.has(key)) return routeCache.get(key);
  const p = (async () => {
    try { return await legViaServer(a, b, profile); }
    catch (e) { if (e.noRoad) throw e; console.warn('[route] сервер /api/route не построил участок:', e.message || e); return await legViaBrowser(a, b, profile); }
  })();
  routeCache.set(key, p);
  p.catch(() => routeCache.delete(key));
  return p;
}

function ensureMap() {
  if (lmap) return;
  lmap = L.map('bigmap', {zoomControl: true});
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(lmap);
  lmap.createPane('roadCasing').style.zIndex = 410;   // между плиткой (200) и маркерами (600)
  lmap.createPane('roadLine').style.zIndex = 420;
  lgroup = L.layerGroup().addTo(lmap);
}

/* Вкладки дней над картой: «Все дни | День 1 | День 2». days = [{v, t, c}], active = v выбранного дня или null, pick(v) — выбор */
function setDayTabs(days, active, pick) {
  const el = document.getElementById('mpDays');
  if (!el) return;
  if (days.length < 2) { el.hidden = true; el.innerHTML = ''; return; }
  const list = [{v: null, t: 'Все дни'}, ...days];
  el.hidden = false;
  el.innerHTML = list.map((b, i) => `<button type="button" data-i="${i}" class="${b.v === active ? 'on' : ''}">${b.c ? `<i style="background:${b.c}"></i>` : ''}${esc(b.t)}</button>`).join('');
  el.onclick = e => { const b = e.target.closest('button'); if (b) pick(list[+b.dataset.i].v); };
}

async function openMap(plan, dayIdx) {
  dayIdx = Number.isInteger(dayIdx) && plan.days[dayIdx] ? dayIdx : null;   // null — показать все дни
  if (typeof drawStop === 'function') drawStop();
  const my = ++runId;
  const app = document.getElementById('app');
  app.classList.add('map-open');
  document.getElementById('mpTitle').textContent = plan.title;
  document.getElementById('mpSub').textContent = dayIdx == null ? plan.days.map(d => d.date).join(' – ') : `${plan.days[dayIdx].label} · ${plan.days[dayIdx].date}`;
  setDayTabs(plan.days.map((d, i) => ({v: i, t: d.label, c: dayColor(i + 1)})), dayIdx, v => openMap(plan, v));
  ensureMap(); lgroup.clearLayers();

  const stops = [];
  plan.days.forEach((d, di) => { if (dayIdx != null && di !== dayIdx) return; d.stops.forEach(s => stops.push({...s, day: di, dayLabel: d.label})); });
  const groups = mergePoints(stops);
  const info = document.getElementById('mpInfo');
  info.innerHTML = '<div class="mp-sum"><span>Строю маршрут…</span></div>';

  // маркеры (совпадающие точки — один маркер «1·6»)
  groups.forEach(g => {
    const label = g.visits.map(v => v.n).join('·');
    L.marker([g.lat, g.lng], {icon: L.divIcon({className: '', html: `<div class="m-pin">${label}</div>`, iconSize: null, iconAnchor: [14, 13]})})
      .addTo(lgroup).bindPopup(`<b>${esc(g.name)}</b><br>${g.visits.map(v => `${esc(v.day)}, ${esc(v.time)}`).join('<br>')}`);
  });
  const all = stops.map(s => [s.lat, s.lng]);
  setTimeout(() => { lmap.invalidateSize(); if (all.length) lmap.fitBounds(all, {padding: [40, 40]}); }, 380);

  // список точек
  const legend = groups.map(g => `<div class="pt" data-ll="${g.lat},${g.lng}"><span class="m-pin pin">${g.visits.map(v => v.n).join('·')}</span>
    <div><b>${esc(g.name)}</b><small>${g.visits.map(v => `${esc(v.day)} ${esc(v.time)}`).join(' · ')}</small></div></div>`).join('');

  // настоящий маршрут по участкам
  const profile = PROFILE[plan.transport] || 'routed-car';
  let ferry = 0, failed = 0;
  const legs = [];
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1], b = stops[i];
    if (hav(a, b) < 0.05) continue;
    legs.push({a, b, ferry: !!a.water !== !!b.water});
  }
  const bounds = [];
  legs.forEach(leg => {
    const color = DAY_COLORS[leg.b.day % DAY_COLORS.length];
    const dashed = {color: '#8A8F98', weight: 3, dashArray: '6 8', opacity: .85};
    if (leg.ferry) {
      ferry++;
      leg.line = L.polyline([[leg.a.lat, leg.a.lng], [leg.b.lat, leg.b.lng]], {pane: 'roadLine', color, weight: 4, dashArray: '2 9', lineCap: 'round'}).addTo(lgroup);
      bounds.push([leg.a.lat, leg.a.lng], [leg.b.lat, leg.b.lng]);
      return;
    }
    // Не изображаем прямую между маркерами как дорогу. Слой добавляется только
    // после ответа маршрутизатора с геометрией реального пути.
    leg.line = null;
    leg.color = color;
  });
  await Promise.all(legs.map(async leg => {
    if (leg.ferry) return;
    try {
      const r = await fetchLeg(leg.a, leg.b, (leg.a.water && leg.b.water) ? 'routed-foot' : profile);
      if (my !== runId) return;
      casing(r.coords);
      leg.line = L.polyline(r.coords, {pane: 'roadLine', color: leg.color, weight: 5, opacity: 1, dashArray: null, lineCap: 'round', lineJoin: 'round'}).addTo(lgroup);
      leg.coords = r.coords;
    } catch (e) {
      failed++;
      if (leg.line) { leg.line.remove(); leg.line = null; }
    }
  }));
  if (my !== runId) return; // пока грузилось, открыли другой маршрут
  legs.forEach(l => bounds.push(...(l.coords || [[l.a.lat, l.a.lng], [l.b.lat, l.b.lng]])));
  if (bounds.length) setTimeout(() => lmap.fitBounds(bounds, {padding: [40, 40]}), 420);

  const sum = [];
  if (ferry) sum.push('<span>Пунктир — переход на катере</span>');
  if (failed) sum.push('<span class="warn">Не удалось построить часть дорог. Проверьте подключение или выберите другие точки.</span>');
  info.innerHTML = (sum.length ? `<div class="mp-sum">${sum.join('')}</div>` : '') + legend;
}

function closeMap() {
  if (typeof drawStop === 'function') drawStop();
  runId++;
  document.getElementById('app').classList.remove('map-open');
}

document.addEventListener('click', e => {
  const pt = e.target.closest('.pt');
  if (pt && lmap && !e.target.closest('.dx')) { const [la, lo] = pt.dataset.ll.split(',').map(Number); lmap.flyTo([la, lo], 12, {duration: .6}); }
});

/* Карта для ответов настоящего агента: data = {markers: [{name, lat, lon, day}], routes: []}.
   markers — ТОЛЬКО точки итогового плана в порядке посещения (их выбирает сервер по строке ROUTE).
   Между КАЖДОЙ парой соседних точек (в том числе между днями) рисуется дорога: готовую геометрию присылает сервер
   (routes[]), недостающую подтягивает браузер. Получается один сплошной путь от старта до финиша;
   Время в пути на карте не показываем, только расстояние. Старые сохранённые ответы (routes[].coords без from/to) рисуются как раньше. */
async function openMapData(data, day) {
  if (typeof drawStop === 'function') drawStop();
  const my = ++runId;
  document.getElementById('app').classList.add('map-open');
  const markers = (data.markers || []).filter(m => Number.isFinite(m.lat) && Number.isFinite(m.lon)).slice(0, 40);
  const saved = (data.routes && data.routes.length ? data.routes : (data.route ? [data.route] : [])).filter(r => r && r.coords && r.coords.length > 1);
  const days = [...new Set(markers.map(m => m.day || 1))].sort((a, b) => a - b);
  const dayCount = days.length;
  day = days.includes(day) ? day : null;                  // null — показать все дни
  const shown = day == null ? markers : markers.filter(m => (m.day || 1) === day);   // точки выбранного дня
  document.getElementById('mpTitle').textContent = 'Маршрут';
  document.getElementById('mpSub').textContent = [day != null ? `День ${day}` : '', `точек: ${shown.length}`, dayCount > 1 && day == null ? `дней: ${dayCount}` : ''].filter(Boolean).join(' · ');
  setDayTabs(days.map(d => ({v: d, t: `День ${d}`, c: dayColor(d)})), day, v => openMapData(data, v));
  ensureMap(); lgroup.clearLayers();

  const bounds = shown.map(m => [m.lat, m.lon]);
  shown.forEach((m, i) => L.marker([m.lat, m.lon], {icon: L.divIcon({className: '', html: `<div class="m-pin" style="background:${dayColor(m.day)}">${i + 1}</div>`, iconSize: null, iconAnchor: [14, 13]})})
    .addTo(lgroup).bindPopup(`<b>${esc(m.name)}</b>${dayCount > 1 ? `<br>День ${m.day || 1}` : ''}`));
  const fit = () => { if (my === runId && bounds.length) { lmap.invalidateSize(); lmap.fitBounds(bounds, {padding: [40, 40], maxZoom: 14}); } };
  setTimeout(fit, 380);

  let lastDay = null;
  const legend = shown.map((m, i) => {
    const head = dayCount > 1 && day == null && (m.day || 1) !== lastDay ? `<div class="day-head"><i style="background:${dayColor(m.day)}"></i>День ${m.day || 1}</div>` : '';
    lastDay = m.day || 1;
    return head + `<div class="pt" data-i="${i}" data-ll="${m.lat},${m.lon}"><span class="m-pin pin" style="background:${dayColor(m.day)}">${i + 1}</span>
    <div><b>${esc(m.name)}</b></div></div>`;
  }).join('');
  const info = document.getElementById('mpInfo');

  // Участки между соседними точками ВСЕГО маршрута, по порядку. Рисуем СРАЗУ (серым пунктиром, пока дороги нет), дорогу подтягиваем по мере ответов.
  const fromSaved = r => {
    const pts = r.coords.map(([lon, lat]) => [lat, lon]);
    return {pts, day: r.day || 1, from: r.from, to: r.to, note: r.note, ok: !r.gap, noRoad: r.reason === 'no_road', dist: (r.distance_km || 0) * 1000,
            a: {lat: pts[0][0], lng: pts[0][1]}, b: {lat: pts[pts.length - 1][0], lng: pts[pts.length - 1][1]}};
  };
  const segs = [];
  if (markers.length < 2 && saved.length) {            // старый формат: только готовые линии
    saved.forEach(r => { const s = fromSaved(r); segs.push(s); });
  } else {
    let k = 0;                                          // сервер строит участки в том же порядке, что и точки: сопоставляем по названиям
    for (let i = 1; i < markers.length; i++) {
      const a = markers[i - 1], b = markers[i];
      if (hav({lat: a.lat, lng: a.lon}, {lat: b.lat, lng: b.lon}) < 0.05) {          // та же точка (например, ночлег и старт следующего дня)
        continue;
      }
      const r = saved[k], hit = r && r.from === a.name && r.to === b.name;
      if (hit) k++;
      const seg = hit ? fromSaved(r) : {pts: [], day: b.day || 1, from: a.name, to: b.name, ok: false, noRoad: false, dist: 0,
                                         a: {lat: a.lat, lng: a.lon}, b: {lat: b.lat, lng: b.lon}};
      seg.da = a.day || 1; seg.db = b.day || 1;         // дни начала и конца участка
      segs.push(seg);
    }
  }
  // выбран один день: оставляем только дороги внутри этого дня (переезды между днями видны на вкладке «Все дни»)
  if (day != null) for (let i = segs.length - 1; i >= 0; i--) {
    const s = segs[i];
    if (!(s.da != null ? s.da === day && s.db === day : s.day === day)) segs.splice(i, 1);
  }
  const style = s => ({pane: 'roadLine', color: dayColor(s.day), weight: 5, opacity: 1, dashArray: null, lineCap: 'round', lineJoin: 'round'});
  const popup = s => `${esc(s.from || '')} → ${esc(s.to || '')}<br>` + (s.ok
    ? `${fmtDist(s.dist)}${s.note ? '<br>' + esc(s.note) : ''}`
    : esc(s.note || (s.noRoad ? 'по дорогам не добраться (возможно, нужен паром или катер)' : 'маршрут по дорогам строится или не построен')));
  segs.forEach(s => {
    s.baseOpacity = .9;
    // Не рисуем прямую между точками как будто это дорога. Слой появляется только после получения геометрии дороги.
    s.layer = s.ok && s.pts.length > 1 ? L.polyline(s.pts, style(s)).addTo(lgroup).bindPopup(popup(s)) : null;
    if (s.ok && s.pts.length > 1) { casing(s.pts); bounds.push(...s.pts); }
  });

  const summary = () => {
    const ok = segs.filter(s => s.ok), waiting = segs.filter(s => !s.ok && !s.noRoad && s.busy), bad = segs.filter(s => !s.ok && !s.busy);
    const parts = [];
    if (waiting.length) parts.push('<span>Строю дорогу…</span>');
    if (ok.length && dayCount > 1) {
      const byDay = {};
      ok.forEach(s => { byDay[s.day] = (byDay[s.day] || 0) + s.dist; });
      Object.keys(byDay).sort((x, y) => x - y).forEach(d => parts.push(`<span><i class="dot" style="background:${dayColor(+d)}"></i>День ${d}: ${fmtDist(byDay[d])}</span>`));
    } else if (ok.length) parts.push(`<span>По дорогам: ${fmtDist(ok.reduce((t, s) => t + s.dist, 0))}</span>`);
    if (bad.length) parts.push(`<span class="warn">${bad.length} ${bad.length === 1 ? 'участок' : 'участка(ов)'} без дороги или без ответа сервиса — серый пунктир</span>`);
    info.innerHTML = (parts.length ? `<div class="mp-sum">${parts.join('')}</div>` : '') + legend;
  };
  const todo = segs.filter(s => !s.ok && !s.noRoad);
  todo.forEach(s => { s.busy = true; });
  summary();
  setTimeout(fit, 420);

  await Promise.all(todo.map(async s => {
    try {
      const r = await fetchLeg(s.a, s.b, 'routed-car');
      if (my !== runId) return;
      Object.assign(s, {pts: r.coords, ok: true, dist: r.dist, baseOpacity: .9});
      casing(r.coords);
      if (s.layer) s.layer.remove();
      s.layer = L.polyline(r.coords, style(s)).addTo(lgroup).bindPopup(popup(s));
      bounds.push(...r.coords);
    } catch (e) {
      if (e && e.noRoad) s.noRoad = true;
      s.note = e && e.noRoad ? 'По дорогам проезда нет; возможно, нужен паром или катер' : 'Не удалось получить маршрут по дорогам';
    } finally {
      s.busy = false;
      if (my === runId) summary();
    }
  }));
  if (my !== runId) return;
  setTimeout(fit, 100);
}
