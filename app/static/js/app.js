const $ = s => document.querySelector(s);
const app = $('#app'), input = $('#input'), sendBtn = $('#send'), composer = $('#composer');
const newState = () => ({showSteps: true, chats: []});

let user = null, state = newState(), current = null, busy = false;
const pending = {};          // chatId -> {t0}: чаты, в которых агент ещё отвечает (см. mountLive)
let thinkTimer = null;

/* ---------- API ---------- */
async function api(path, method = 'GET', body) {
  const r = await fetch(path, {method, credentials: 'same-origin',
    headers: body ? {'Content-Type': 'application/json'} : undefined, body: body ? JSON.stringify(body) : undefined});
  if (r.status === 401 && !path.startsWith('/api/auth/')) { showAuth(); throw new Error('auth'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' && j.detail ? j.detail : (r.status === 422 ? 'Проверьте введённые данные' : 'Ошибка сервера'));
  return j;
}
let saveT;
function save() {
  clearTimeout(saveT);
  saveT = setTimeout(() => {
    // сервер хранит не больше ~1 МБ на пользователя: при переполнении удаляем самые старые чаты
    while (state.chats.length > 1 && JSON.stringify(state).length > 800000) state.chats.pop();
    api('/api/state', 'PUT', {data: state}).catch(() => {});
  }, 500);
}
const chatById = id => state.chats.find(c => c.id === id);
/* Последние реплики чата для модели: вопросы пользователя и текстовые ответы агента (отказы и ошибки не передаём) */
function historyFor(chat) {
  const out = [];
  for (const m of chat.msgs) {
    if (m.role === 'user') out.push({role: 'user', content: m.text});
    else if (m.data && (m.data.type === 'info' || m.data.type === 'plan') && (m.data.text || m.data.summary))
      out.push({role: 'assistant', content: String(m.data.text || m.data.summary).slice(0, 1500)});
  }
  return out.slice(-10);
}

/* ---------- Auth ---------- */
let mode = 'login';
function setMode(m) {
  mode = m;
  $('#tabLogin').classList.toggle('on', m === 'login'); $('#tabReg').classList.toggle('on', m === 'reg');
  $('#nameRow').hidden = m !== 'reg';
  $('#aName').required = $('#aSurname').required = m === 'reg';
  $('#aSubmit').textContent = m === 'reg' ? 'Создать аккаунт' : 'Войти';
  $('#aPass').autocomplete = m === 'reg' ? 'new-password' : 'current-password';
  $('#aErr').hidden = true;
}
$('#tabLogin').onclick = () => setMode('login'); $('#tabReg').onclick = () => setMode('reg');
function showAuth() { $('#auth').hidden = false; closeMap(); }
$('#authForm').onsubmit = async e => {
  e.preventDefault();
  const err = $('#aErr'), btn = $('#aSubmit'); err.hidden = true; btn.disabled = true;
  try {
    const body = {email: $('#aEmail').value, password: $('#aPass').value};
    if (mode === 'reg') { body.name = $('#aName').value; body.surname = $('#aSurname').value; }
    const r = await api(mode === 'reg' ? '/api/auth/register' : '/api/auth/login', 'POST', body);
    $('#aPass').value = '';
    await start(r.user);
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  btn.disabled = false;
};
async function start(u) {
  user = u;
  const s = await api('/api/state');
  state = s.data || newState();
  if (!Array.isArray(state.chats)) state.chats = [];
  // демо-чаты из прежней версии (пустые «seed…») больше не нужны
  state.chats = state.chats.filter(c => !(String(c.id).startsWith('seed') && !(c.msgs || []).length));
  if (!s.data) save();
  setName(); $('#auth').hidden = true; showWelcome();
}
async function logout() {
  await api('/api/auth/logout', 'POST').catch(() => {});
  user = null; state = newState(); current = null; $('#thread').innerHTML = '';
  setMode('login'); showAuth();
}

/* ---------- layout ---------- */
const isDesktop = () => matchMedia('(min-width:901px)').matches;
function placeComposer() {
  const chatting = app.classList.contains('chatting');
  const target = (!chatting && isDesktop()) ? $('#slotWelcome') : $('#dock');
  if (composer.parentElement !== target) target.insertBefore(composer, target.firstChild);
}
addEventListener('resize', placeComposer);
function setName() {
  const full = [user.name, user.surname].filter(Boolean).join(' ');
  $('#pname').textContent = full; $('#hname').textContent = user.name;
  $('#avatar').textContent = (user.name[0] || '?').toUpperCase();
}
/* Короткое название чата (2–3 слова): для плана берём его заголовок, иначе — места/ключевые слова из вопроса */
const STOP = new Set('и в во на по до из от за к с со у о об для при про или а но как что где когда мне нам хочу хотим хотел хотела нужно надо можно подскажи расскажи составь сделай построй покажи пожалуйста это эти тот так там тут есть быть ли же бы не мой наш нас выходные поездка поездку маршрут маршруты день дня дней два три один одну можно'.split(' '));
function shortTitle(text, data) {
  const clean = s => String(s).replace(/[^\p{L}\p{N}\s-]/gu, ' ').split(/\s+/).filter(Boolean);
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  if (data && data.type === 'plan' && data.title) {           // заголовок плана: «Выходные в Лазовском»
    const t = clean(data.title).slice(0, 3).join(' ');
    if (t) return cap(t);
  }
  const words = clean(text), PREP = new Set(['в', 'на', 'из', 'до', 'по', 'от', 'через']);
  const sig = w => w.length > 2 && !STOP.has(w.toLowerCase()) && !/^\d+$/.test(w);
  const places = [];   // названия мест (с заглавной, не первое слово) вместе с предлогом перед ними
  words.forEach((w, i) => {
    if (i > 0 && /^\p{Lu}/u.test(w) && sig(w)) {
      const prev = words[i - 1].toLowerCase();
      places.push((PREP.has(prev) ? prev + ' ' : '') + w);
    }
  });
  let t = places.slice(0, 2).join(' ') || words.filter(sig).slice(0, 2).join(' ');
  return t ? cap(t) : 'Новый маршрут';
}
function renderRecent() {
  $('#recent').innerHTML = state.chats.map(c =>
    `<button data-id="${esc(c.id)}" class="${c.id === current ? 'active' : ''}">${ico('route')}<span>${esc(c.title)}</span></button>`).join('');
}
function showWelcome() {
  current = null; stopThinking(); closeMap(); app.classList.remove('chatting');
  $('#welcome').hidden = false; $('#chat').hidden = true; $('#thread').innerHTML = '';
  placeComposer(); renderRecent(); input.focus();
}
function showChat(id) {
  current = id; closeMap(); app.classList.add('chatting');
  $('#welcome').hidden = true; $('#chat').hidden = false; $('#thread').innerHTML = '';
  placeComposer(); renderRecent();
  chatById(id).msgs.forEach(m => appendMsg(m));
  mountLive(id);   // если в этом чате агент ещё отвечает — снова показать «Бухта думает»
  scrollDown(); closeSide();
}
const scrollDown = () => { const c = $('#chat'); c.scrollTop = c.scrollHeight; };

/* ---------- messages ---------- */
function appendMsg(m) {
  const th = $('#thread');
  if (m.role === 'user') { th.insertAdjacentHTML('beforeend', `<div class="msg-u">${esc(m.text)}</div>`); return; }
  const r = m.data, mapId = 'p' + Math.random().toString(36).slice(2, 8);
  const stepsHtml = state.showSteps ? `<div class="steps done"><div class="h"><span>Ход работы агента · ${r.steps.length} шагов</span><span>${ico('chev')}</span></div><ul>${r.steps.map(s => `<li>${ico('check')}${esc(s)}</li>`).join('')}</ul></div>` : '';
  th.insertAdjacentHTML('beforeend', `<div class="msg-a"><svg class="logo"><use href="#logo"/></svg><div class="a-body">${stepsHtml}${renderAnswer(r, mapId)}</div></div>`);
  const st = th.lastElementChild.querySelector('.steps');
  if (st) st.querySelector('.h').onclick = () => st.classList.toggle('open');
}

/* Сервер отвечает одним куском, поэтому реальные шаги агента видны только после ответа. Пока ждём — показываем,
   что запрос выполняется: анимация, секундомер и подсказки при долгом ожидании.
   Запрос привязан к чату (pending[chatId]): если переключиться на другой чат и вернуться, индикатор появится снова,
   а готовый ответ попадёт в свой чат, даже если сейчас открыт другой. */
const WAIT_HINTS = [[8, 'Собираю данные и составляю ответ…'], [25, 'Составляю подробный план — это может занять до минуты'],
                    [70, 'Дольше обычного, но запрос ещё выполняется']];
function stopThinking() { clearInterval(thinkTimer); thinkTimer = null; }

function mountLive(chatId) {   // рисует блок «Бухта думает» в открытом чате (если он ждёт ответа)
  stopThinking();
  $('#live')?.remove();
  const pend = pending[chatId];
  if (!pend || current !== chatId) return;
  $('#thread').insertAdjacentHTML('beforeend', `<div class="msg-a" id="live"><svg class="logo thinking"><use href="#logo"/></svg><div class="a-body"><div class="steps live" role="status" aria-live="polite"><div class="h"><span class="think">Бухта думает<span class="dots"><i></i><i></i><i></i></span></span><span class="elapsed" id="liveTime">0 с</span></div><div class="bar"><i></i></div><div class="hint" id="liveHint"></div></div></div></div>`);
  const tick = () => {
    const s = Math.floor((Date.now() - pend.t0) / 1000);
    const time = $('#liveTime'), hint = $('#liveHint');
    if (time) time.textContent = s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${String(s % 60).padStart(2, '0')} с`;
    if (hint) hint.textContent = (WAIT_HINTS.filter(([from]) => s >= from).pop() || [0, ''])[1];
  };
  tick(); thinkTimer = setInterval(tick, 1000);
  scrollDown();
}

async function ask(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true; sendBtn.disabled = true;
  if (!current) {
    const c = {id: 'c' + Date.now(), title: shortTitle(text), msgs: []};
    state.chats.unshift(c); showChat(c.id);
  }
  const chatId = current, chat = chatById(chatId);
  const history = historyFor(chat);   // предыдущие реплики — модель помнит контекст диалога
  const um = {role: 'user', text}; chat.msgs.push(um); appendMsg(um); scrollDown();
  input.value = ''; autosize(); updateSend();

  pending[chatId] = {t0: Date.now()};
  mountLive(chatId);
  let data;
  try {
    data = await api('/api/chat', 'POST', {message: text, history});
  } catch (e) {
    if (e.message === 'auth') { delete pending[chatId]; stopThinking(); $('#live')?.remove(); busy = false; return; }
    const known = e.message && e.message !== 'Ошибка сервера' && e.message !== 'Failed to fetch';
    data = {type: 'refusal', title: 'Что-то пошло не так', steps: ['Ошибка'], meta: {},
            text: known ? e.message : 'Не удалось связаться с сервером. Попробуйте ещё раз.'};
  }
  // ответ сохраняем в СВОЙ чат, а на экран выводим, только если этот чат сейчас открыт
  delete pending[chatId];
  const am = {role: 'agent', data}; chat.msgs.push(am);
  if (chat.msgs.length <= 2) { chat.title = shortTitle(text, data); renderRecent(); }
  save();
  if (current === chatId) {
    stopThinking(); $('#live')?.remove();
    appendMsg(am); scrollDown();
  } else if (!current || !pending[current]) {
    toast(`Ответ готов в чате «${chat.title}»`);
  }
  busy = false; updateSend();
}

/* ---------- composer ---------- */
function autosize() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; }
function updateSend() { sendBtn.disabled = busy || !input.value.trim(); }
input.addEventListener('input', () => { autosize(); updateSend(); });
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
sendBtn.onclick = () => ask(input.value);

/* ---------- navigation ---------- */
$('#newChat').onclick = () => { showWelcome(); closeSide(); };
$('#mapClose').onclick = closeMap;
document.addEventListener('click', e => {
  const mb = e.target.closest('[data-map]'); if (mb) { openMap(PLANS[mb.dataset.map], mb.dataset.day != null ? +mb.dataset.day : null); return; }
  const md = e.target.closest('[data-mapdata]'); if (md) { openMapData(MAPS[md.dataset.mapdata], md.dataset.day != null ? +md.dataset.day : null); return; }
  const q = e.target.closest('[data-q]'); if (q) { ask(q.dataset.q); return; }
  const r = e.target.closest('#recent button');
  if (r) {
    const c = chatById(r.dataset.id);
    showChat(c.id);
    return;
  }
  if (!e.target.closest('.profile-wrap')) toggleMenu(false);
});
document.addEventListener('keydown', e => {   // заголовок дня в плане — не <button>, поэтому Enter/пробел обрабатываем сами
  const d = e.target.closest && e.target.closest('.day-h[role="button"]');
  if (d && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); d.click(); }
});
function openSide() { app.classList.add('side-open'); }
function closeSide() { app.classList.remove('side-open'); }
$('#openSide').onclick = openSide; $('#closeSide').onclick = closeSide; $('#overlay').onclick = closeSide;

/* ---------- profile menu & modals ---------- */
function toggleMenu(open) { $('#menu').hidden = !open; $('#profile').setAttribute('aria-expanded', open); }
$('#profile').onclick = e => { e.stopPropagation(); toggleMenu($('#menu').hidden); };
$('#menu').onclick = e => { const b = e.target.closest('button'); if (b) { toggleMenu(false); modals[b.dataset.act](); } };
function modal(html) {
  $('#modal').innerHTML = html; $('#modalBack').hidden = false;
  $('#modalBack').onclick = e => { if (e.target.id === 'modalBack' || e.target.dataset.close != null) $('#modalBack').hidden = true; };
}
const modals = {
  settings() {
    modal(`<h2>Настройки</h2><p>${esc([user.surname, user.name].filter(Boolean).join(' '))} · ${esc(user.email)}</p>
      <label><input type="checkbox" id="sSteps" ${state.showSteps?'checked':''}> Показывать шаги агента</label>
      <div class="btns"><button class="btn" data-close>Отмена</button><button class="btn p" id="sSave">Сохранить</button></div>`);
    $('#sSave').onclick = () => {
      state.showSteps = $('#sSteps').checked; save(); $('#modalBack').hidden = true;
      if (current) showChat(current); toast('Настройки сохранены');
    };
  },
  about() {
    modal(`<h2>О проекте и справка</h2>
      <p>«Бухта» — демо AI-ассистента для путешествий по Приморскому краю. Ответы формирует языковая модель, которая вызывает инструменты: погода (Open-Meteo), места (OpenStreetMap/Google Places), маршруты (OSRM/OpenRouteService). Данные об АЗС, жилье и расписаниях — демонстрационные и приблизительные: проверяйте их перед поездкой.</p>
      <p style="margin-top:12px"><b>Как пользоваться</b></p>
      <ul><li>Опишите поездку: «Выходные в Лазовском с ночёвкой, не больше двух дней»</li>
      <li>Можно уточнять: ассистент помнит предыдущие сообщения чата</li>
      <li>Транспорт выбирается внизу, в поле ввода</li>
      <li>Если в ответе есть места или маршрут, нажмите «Открыть карту» — карта откроется справа</li>
      <li>На посторонние запросы ассистент вежливо откажет</li></ul>
      <div class="btns"><button class="btn p" data-close>Понятно</button></div>`);
  },
  clear() {
    modal(`<h2>Очистить историю?</h2><p>Все чаты будут удалены.</p>
      <div class="btns"><button class="btn" data-close>Отмена</button><button class="btn d" id="doClear">Очистить</button></div>`);
    $('#doClear').onclick = () => { state.chats = []; save(); $('#modalBack').hidden = true; showWelcome(); toast('История очищена'); };
  },
  logout,
};
function toast(t) {
  const el = $('#toast'); el.textContent = t; el.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => el.hidden = true, 2200);
}

/* ---------- init ---------- */
updateSend();
api('/api/auth/me').then(r => start(r.user)).catch(() => { setMode('login'); showAuth(); });
