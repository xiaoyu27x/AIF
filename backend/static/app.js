const box = document.querySelector('#messages');
const form = document.querySelector('#chat');
const roles = document.querySelector('#roles');
const editor = document.querySelector('#editor');
const profileEditor = document.querySelector('#profile-editor');
const preferencesEditor = document.querySelector('#preferences-editor');
const palette = ['#5a2946','#2f456d','#365a58','#70463b','#4e5e39','#673447','#3d4b78','#714d32','#315a62','#5b365d'];
const rings = ['#bd799d','#7797cc','#77aaa3','#c78b76','#93ae6e','#bd7893','#839bd1','#c59773','#72aaa9','#b582b9'];
let current = 1;
let personas = [];
let modeSelector;
let sillyTavernOnline = false;
let uiLanguage = 'zh-CN';
const copy = {
  'zh-CN': { language: '语言', myName: '我的名字', editRole: '编辑角色', clearHistory: '清空记录', logout: '退出', send: '发送', save: '保存', cancel: '取消', changeAvatar: '更换头像', languageSettings: '语言设置', chatLanguage: '对话语言', uiLanguage: '界面语言', message: '说点什么…', thinking: '正在想…', network: '网络似乎断开了，稍后再试。', usage: '用量', model: '模型', continuous: '连续模式', time: '时间模式', affection: '情感', friendship: '友谊' },
  'en-US': { language: 'Language', myName: 'My name', editRole: 'Edit role', clearHistory: 'Clear history', logout: 'Log out', send: 'Send', save: 'Save', cancel: 'Cancel', changeAvatar: 'Change avatar', languageSettings: 'Language settings', chatLanguage: 'Chat language', uiLanguage: 'Interface language', message: 'Say something…', thinking: 'Thinking…', network: 'Network appears disconnected. Please try again.', usage: 'Usage', model: 'Model', continuous: 'Continuous', time: 'Time mode', affection: 'Affection', friendship: 'Friendship' },
  'ja-JP': { language: '言語', myName: '自分の名前', editRole: 'キャラクターを編集', clearHistory: '履歴を削除', logout: 'ログアウト', send: '送信', save: '保存', cancel: 'キャンセル', changeAvatar: 'アバターを変更', languageSettings: '言語設定', chatLanguage: '会話言語', uiLanguage: '表示言語', message: 'メッセージを入力…', thinking: '考え中…', network: 'ネットワーク接続を確認してください。', usage: '使用量', model: 'モデル', continuous: '連続モード', time: '時間モード', affection: '好感度', friendship: '友情度' },
  'ko-KR': { language: '언어', myName: '내 이름', editRole: '캐릭터 편집', clearHistory: '기록 삭제', logout: '로그아웃', send: '보내기', save: '저장', cancel: '취소', changeAvatar: '아바타 변경', languageSettings: '언어 설정', chatLanguage: '대화 언어', uiLanguage: '화면 언어', message: '메시지를 입력하세요…', thinking: '생각 중…', network: '네트워크 연결을 확인해 주세요.', usage: '사용량', model: '모델', continuous: '연속 모드', time: '시간 모드', affection: '감정', friendship: '우정' },
};
function tr(key) { return (copy[uiLanguage] || copy['zh-CN'])[key] || copy['zh-CN'][key] || key; }
function applyUiLanguage(language) {
  uiLanguage = copy[language] ? language : 'zh-CN';
  document.documentElement.lang = uiLanguage;
  document.querySelectorAll('[data-i18n]').forEach(node => { node.childNodes[0].nodeValue = tr(node.dataset.i18n); });
  form.elements.message.placeholder = tr('message');
  if (modeSelector) {
    modeSelector.options[0].text = tr('continuous');
    modeSelector.options[1].text = tr('time');
  }
  const person = personas.find(item => item.id === current);
  if (person) renderRelationship(person);
}

function scrollMessagesToEnd() {
  const move = () => {
    const last = box.lastElementChild;
    box.scrollTop = box.scrollHeight;
    if (last) last.scrollIntoView({ block: 'end', inline: 'nearest' });
    box.scrollTop = box.scrollHeight;
  };
  requestAnimationFrame(move);
  setTimeout(move, 80);
  setTimeout(move, 260);
}

const style = document.createElement('style');
style.textContent = `.chat-panel header{display:grid!important;grid-template-columns:auto minmax(240px,1fr) repeat(7,auto)!important;gap:14px!important;align-items:start!important;min-height:104px!important;max-height:104px!important}.chat-panel header #title{padding:7px 12px;border:1px solid #f1b6d3;border-radius:999px;color:#fff0f7;white-space:nowrap;font:700 19px serif}.chat-panel header #description{display:block!important;min-width:0!important;height:74px!important;max-height:74px!important;margin:0!important;color:#d8c8e3!important;font:15px/1.65 sans-serif!important;white-space:pre-line!important;overflow-y:auto!important;overflow-x:hidden!important}.conversation-mode,.model-runtime{background:#00000028;border:1px solid #ffffff38;border-radius:18px;color:#fff0f7;padding:9px 12px;font:inherit;cursor:pointer;white-space:nowrap}.model-runtime.gpu{border-color:#7dd8a5;color:#b9ffd2}.model-runtime.cpu{border-color:#ffb36b;color:#ffd2aa}`;
document.head.append(style);
const relationshipStyle = document.createElement('style');
relationshipStyle.textContent = `.relationship{margin-top:14px;padding:10px 12px;background:#00000035;border:1px solid #ffffff25;border-radius:13px;min-width:210px}.relationship-row{display:grid;grid-template-columns:62px 1fr 32px;align-items:center;gap:8px;margin:5px 0;font-size:13px}.relationship-track{height:7px;background:#ffffff26;border-radius:99px;overflow:hidden}.relationship-fill{height:100%;border-radius:99px}.relationship-fill.affection{background:linear-gradient(90deg,#f4a6c8,#ed6eaa)}.relationship-fill.friendship{background:linear-gradient(90deg,#92c9ed,#6598db)}`;
document.head.append(relationshipStyle);

function showExpression(bubble, expression) {
  const icons = { happy: '😊', shy: '☺️', sad: '😔', angry: '😠', neutral: '🙂' };
  const previous = bubble.querySelector('.expression');
  if (previous) previous.remove();
  const chip = document.createElement('small');
  chip.className = 'expression';
  chip.textContent = icons[expression] || icons.neutral;
  chip.title = '当前表情';
  bubble.prepend(chip);
}

function addBubble(role, text) {
  const bubble = document.createElement('article');
  bubble.className = `bubble ${role}`;
  const words = document.createElement('span');
  words.textContent = text;
  bubble.append(words);
  box.append(bubble);
  scrollMessagesToEnd();
  return { bubble, words };
}

function applyTheme(id) {
  const index = Math.max(0, personas.findIndex(person => person.id === id));
  const theme = palette[index];
  document.body.style.background = '#171326';
  document.querySelector('.workspace').style.background = 'transparent';
  document.querySelector('.portrait-panel').style.background = theme;
  document.querySelector('.chat-panel').style.background = `linear-gradient(135deg,${theme},#171326 82%)`;
  const title = document.querySelector('#title');
  title.style.borderColor = rings[index];
  title.style.boxShadow = `0 0 0 3px ${rings[index]}33`;
  roles.querySelectorAll('button').forEach((button, i) => {
    button.style.background = palette[i];
    button.style.outline = i === index ? `3px solid ${rings[i]}` : 'none';
  });
}

function renderRelationship(person) {
  let panel = document.querySelector('#relationship');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'relationship';
    panel.className = 'relationship';
    document.querySelector('.portrait-info').append(panel);
  }
  const affection = Math.max(0, Math.min(100, Number(person.affection ?? 35)));
  const friendship = Math.max(0, Math.min(100, Number(person.friendship ?? 50)));
  panel.innerHTML = `<div class="relationship-row"><span>${tr('affection')}</span><i class="relationship-track"><b class="relationship-fill affection" style="width:${affection}%"></b></i><strong>${affection}</strong></div><div class="relationship-row"><span>${tr('friendship')}</span><i class="relationship-track"><b class="relationship-fill friendship" style="width:${friendship}%"></b></i><strong>${friendship}</strong></div>`;
}

async function refreshSillyTavernStatus() {
  try {
    const response = await fetch('/api/sillytavern/status');
    const status = await response.json();
    sillyTavernOnline = Boolean(status.available);
  } catch { sillyTavernOnline = false; }
  const hint = document.querySelector('#role-engine-status');
  if (hint) hint.textContent = sillyTavernOnline
    ? 'SillyTavern 服务已连接；此角色会使用其角色卡与扩展。'
    : 'SillyTavern 当前未连接；可先保存选择，聊天时会明确提示需要启动服务。';
}

async function selectRole(id) {
  const person = personas.find(persona => persona.id === id);
  if (!person) return;
  current = id;
  applyTheme(id);
  document.title = person.name;
  document.querySelector('#title').textContent = `✿ ${person.name} ✿`;
  document.querySelector('#portrait-name').textContent = person.name;
  renderRelationship(person);
  if (modeSelector) modeSelector.value = person.conversation_mode === 'time' ? 'time' : 'continuous';
  document.querySelector('#portrait').src = `${person.avatar}?v=${Date.now()}`;
  const lines = (person.prompt || '').replace(/\s+/g, ' ').replace(/你叫[^。]+。/, '').split(/(?<=[。！？.!?])\s*/).filter(Boolean);
  document.querySelector('#description').textContent = lines.slice(0, 3).join('\n') || '温柔、独立，带一点俏皮害羞。';
  box.innerHTML = '';
  const response = await fetch(`/api/history?persona_id=${id}`);
  if (!response.ok) return;
  const history = await response.json();
  history.messages.forEach(message => addBubble(message.role, message.content));
}

async function refreshUsage(button) {
  try {
    const response = await fetch('/api/usage');
    const usage = await response.json();
    button.textContent = `${tr('usage')} ${(usage.total_tokens || 0).toLocaleString()} Tokens`;
  } catch { button.textContent = '用量暂不可用'; }
}

async function refreshModelRuntime(button) {
  try {
    const response = await fetch('/api/model-runtime');
    const runtime = await response.json();
    const processor = runtime.processor || '未知';
    button.textContent = `${tr('model')}：${processor}`;
    button.classList.toggle('gpu', processor === 'GPU');
    button.classList.toggle('cpu', processor === 'CPU');
    button.title = runtime.label || '模型运行位置';
  } catch { button.textContent = '模型：未知'; }
}

async function start() {
  try {
    const profile = await (await fetch('/api/profile')).json();
    applyUiLanguage(profile.ui_language || 'zh-CN');
  } catch { applyUiLanguage('zh-CN'); }
  const response = await fetch('/api/personas');
  if (!response.ok) { location = '/login'; return; }
  personas = (await response.json()).personas;
  personas.forEach(person => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = person.name.slice(0, 2);
    button.title = person.name;
    button.onclick = () => selectRole(person.id);
    roles.append(button);
  });
  const usage = document.createElement('button');
  usage.type = 'button'; usage.className = 'quiet'; usage.title = '本账户模型 Token 用量';
  usage.onclick = () => refreshUsage(usage);
  document.querySelector('.chat-panel header').insertBefore(usage, document.querySelector('#logout'));
  refreshUsage(usage);
  const runtime = document.createElement('button');
  runtime.type = 'button'; runtime.className = 'model-runtime'; runtime.title = '模型实际运行位置，点击刷新';
  runtime.onclick = () => refreshModelRuntime(runtime);
  document.querySelector('.chat-panel header').insertBefore(runtime, document.querySelector('#logout'));
  refreshModelRuntime(runtime);
  setInterval(() => refreshModelRuntime(runtime), 15000);
  modeSelector = document.createElement('select');
  modeSelector.className = 'conversation-mode';
  modeSelector.title = '决定是否按真实时间、工作日与聊天间隔进行对话';
  modeSelector.innerHTML = `<option value="continuous">${tr('continuous')}</option><option value="time">${tr('time')}</option>`;
  modeSelector.onchange = async () => {
    const fd = new FormData(); fd.append('mode', modeSelector.value);
    const response = await fetch(`/api/personas/${current}/mode`, { method: 'POST', body: fd });
    if (!response.ok) {
      modeSelector.value = personas.find(person => person.id === current).conversation_mode || 'continuous';
      return alert('模式保存失败');
    }
    personas.find(person => person.id === current).conversation_mode = modeSelector.value;
  };
  document.querySelector('.chat-panel header').insertBefore(modeSelector, document.querySelector('#logout'));
  const restoreId = Number(sessionStorage.getItem('ai_girlfriend_current_role'));
  if (personas.length) await selectRole(personas.some(person => person.id === restoreId) ? restoreId : personas[0].id);
}

document.querySelector('#edit').onclick = () => {
  const person = personas.find(item => item.id === current);
  if (!person) return alert('角色资料仍在加载，请稍后再试');
  document.querySelector('#role-name').value = person.name;
  document.querySelector('#role-prompt').value = person.prompt;
  document.querySelector('#role-affection').value = person.affection ?? 35;
  document.querySelector('#role-friendship').value = person.friendship ?? 50;
  document.querySelector('#role-gender').value = person.gender === 'male' ? 'male' : 'female';
  document.querySelector('#role-engine').value = person.engine_mode === 'sillytavern' ? 'sillytavern' : 'native';
  refreshSillyTavernStatus();
  if (!editor.open) editor.showModal();
};
document.querySelector('#save-role').onclick = async () => {
  const fd = new FormData();
  fd.append('name', document.querySelector('#role-name').value);
  fd.append('prompt', document.querySelector('#role-prompt').value);
  const person = personas.find(item => item.id === current);
  const affection = document.querySelector('#role-affection').value;
  const friendship = document.querySelector('#role-friendship').value;
  fd.append('affection', affection === '' ? person.affection : affection);
  fd.append('friendship', friendship === '' ? person.friendship : friendship);
  fd.append('gender', document.querySelector('#role-gender').value);
  fd.append('conversation_mode', person.conversation_mode || 'continuous');
  fd.append('engine_mode', document.querySelector('#role-engine').value);
  const response = await fetch(`/api/personas/${current}`, { method: 'POST', body: fd });
  if (!response.ok) { const data = await response.json(); return alert(data.detail || '保存失败'); }
  sessionStorage.setItem('ai_girlfriend_current_role', String(current));
  location.reload();
};
const profileButton = document.querySelector('#edit-profile');
const profileSave = document.querySelector('#save-profile');
if (profileButton && profileEditor && profileSave) {
  profileButton.onclick = async () => {
    const profile = await (await fetch('/api/profile')).json();
    document.querySelector('#profile-name').value = profile.name || '';
    profileEditor.showModal();
  };
  profileSave.onclick = async () => {
    const name = document.querySelector('#profile-name').value.trim();
    if (!name) return alert('请填写你的名字');
    const fd = new FormData(); fd.append('name', name);
    const response = await fetch('/api/profile', { method: 'POST', body: fd });
    const data = await response.json();
    if (!response.ok) return alert(data.detail || '保存失败');
    profileEditor.close(); alert(`已设置为：${data.name}`);
  };
}
const preferencesButton = document.querySelector('#preferences');
const preferencesSave = document.querySelector('#save-preferences');
if (preferencesButton && preferencesEditor && preferencesSave) {
  preferencesButton.onclick = async () => {
    const profile = await (await fetch('/api/profile')).json();
    document.querySelector('#chat-language').value = profile.chat_language || 'zh-CN';
    document.querySelector('#ui-language').value = profile.ui_language || 'zh-CN';
    preferencesEditor.showModal();
  };
  preferencesSave.onclick = async () => {
    const fd = new FormData();
    fd.append('chat_language', document.querySelector('#chat-language').value);
    fd.append('ui_language', document.querySelector('#ui-language').value);
    const response = await fetch('/api/preferences', { method: 'POST', body: fd });
    const data = await response.json();
    if (!response.ok) return alert(data.detail || '保存失败');
    applyUiLanguage(data.ui_language);
    preferencesEditor.close();
    if (modeSelector) modeSelector.value = personas.find(person => person.id === current)?.conversation_mode || 'continuous';
    refreshUsage(document.querySelector('.chat-panel header .quiet:not(#preferences):not(#edit-profile):not(#edit):not(#logout)'));
  };
}
document.querySelector('#avatar-input').onchange = async event => {
  const image = event.target.files[0]; if (!image) return;
  const fd = new FormData(); fd.append('image', image); fd.append('persona_id', current);
  const response = await fetch('/api/avatar', { method: 'POST', body: fd }); const data = await response.json();
  if (!response.ok) return alert(data.detail || '更换头像失败');
  document.querySelector('#portrait').src = `${data.url}?v=${Date.now()}`;
  personas.find(person => person.id === current).avatar = data.url;
};
document.querySelector('#logout').onclick = async () => { await fetch('/api/logout', { method: 'POST' }); location = '/login'; };
document.querySelector('#clear-history').onclick = async () => {
  const person = personas.find(item => item.id === current);
  const name = person?.name || '';
  if (!confirm(`确定清空 ${name} 的聊天记录和相关记忆吗？角色资料、头像、情感和友谊不会删除。`)) return;
  const fd = new FormData(); fd.append('persona_id', current);
  const response = await fetch('/api/history/clear', { method: 'POST', body: fd });
  const data = await response.json();
  if (!response.ok) return alert(data.detail || '清空失败');
  box.innerHTML = '';
};

form.onsubmit = async event => {
  event.preventDefault();
  const text = form.elements.message.value.trim(); if (!text) return;
  const fd = new FormData(); fd.append('message', text); fd.append('persona_id', current);
  addBubble('user', text); form.reset();
  const pending = addBubble('assistant', tr('thinking'));
  try {
    const response = await fetch('/api/chat', { method: 'POST', body: fd }); const data = await response.json();
    pending.words.textContent = response.ok ? data.reply : (data.detail || '稍后再试。');
    if (response.ok) showExpression(pending.bubble, data.expression);
    scrollMessagesToEnd();
    if (response.ok && data.relationship) { Object.assign(personas.find(person => person.id === current), data.relationship); renderRelationship(personas.find(person => person.id === current)); }
  } catch { pending.words.textContent = tr('network'); }
};
form.querySelector('textarea').addEventListener('keydown', event => {
  if (event.altKey && event.key.toLowerCase() === 's') { event.preventDefault(); form.requestSubmit(); }
});

start();
