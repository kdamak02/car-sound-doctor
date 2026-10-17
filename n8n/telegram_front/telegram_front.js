// Telegram front for the Car Sound Doctor chain. Owner: B2. Pure functions (no I/O): the Code nodes of
// n8n/workflows/telegram_front.json embed this file (built by n8n/telegram_front/build_telegram_front.py) and do the HTTP and file work.
// Test: node n8n/telegram_front/test_telegram_front.js
//
// One conversation per chat, stored by the workflow. Stages:
//   idle -> need_car -> need_symptom -> need_context -> (agents, pass 1) -> answering -> (agents, pass 2) -> idle
// handle(state, upd)         -> { state, messages, run }   upd = a Telegram update (message or callback_query)
// format(state, kind, reply) -> { state, messages, run }   reply = answer of the n8n chain (kind: 'audio' | 'answers')
// A message is { text, buttons?: [[{ text, data }]] }. `run` is null or { kind: 'audio'|'answers', case, file_id? }.

const CONTEXTS = { idle: 'Au ralenti', startup: 'Au démarrage', braking: 'Au freinage', driving: 'En roulant' };
const MAX_ROUNDS = 2;
const HELP =
  "Bonjour ! Je suis Car Sound Doctor.\nEnvoyez-moi une note vocale de 5 à 10 secondes près du moteur (sans parler), " +
  'je vous demanderai la voiture, le problème et quand le bruit apparaît, puis je vous poserai quelques vérifications simples.\n' +
  '/annuler pour recommencer.';

function fresh() { return { stage: 'idle' }; }

// "Renault Clio 2015 diesel" -> { make, model, year, fuel } | null
function parseCar(text) {
  const t = String(text || '').trim();
  const y = t.match(/\b(19[89]\d|20[0-4]\d)\b/);
  const f = t.match(/\b(diesel|essence|petrol|hybride|hybrid|[ée]lectrique|electric)\b/i);
  const rest = t.replace(y ? y[0] : '', '').replace(f ? f[0] : '', '').split(/[\s,;]+/).filter(Boolean);
  if (!y || rest.length < 2) return null;
  const fuelMap = { essence: 'petrol', petrol: 'petrol', diesel: 'diesel', hybride: 'hybrid', hybrid: 'hybrid', electrique: 'electric', électrique: 'electric', electric: 'electric' };
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  return { make: cap(rest[0]), model: rest.slice(1).join(' '), year: parseInt(y[0], 10), fuel: f ? fuelMap[f[1].toLowerCase()] : null };
}

// the driver's words already say when the noise happens
function guessContext(text) {
  const t = String(text || '').toLowerCase();
  if (/(d[ée]marr|d[ée]marrage|au d[ée]marrage|clic|ne d[ée]marre)/.test(t)) return 'startup';
  if (/(ralenti|[àa] l.arr[êe]t|moteur tourne)/.test(t)) return 'idle';
  if (/(frein|freinage)/.test(t)) return 'braking';
  if (/(en roulant|[àa] \d+ ?km|sur la route|acc[ée]l[ée]r)/.test(t)) return 'driving';
  return null;
}

const ctxButtons = () => [Object.keys(CONTEXTS).map((k) => ({ text: CONTEXTS[k], data: 'ctx:' + k }))];

function buildCase(s, chatId, now) {
  return {
    case_id: 'tg_' + chatId + '_' + now, chat_id: String(chatId), status: 'extracted', round: 0,
    created_at: new Date(now).toISOString(),
    user_input: { text: `${s.car.make} ${s.car.model} ${s.car.year}. ${s.symptom}`, voice_transcript: null, language: 'fr', audio_file_id: s.file_id },
    vehicle: { make: s.car.make, model: s.car.model, year: s.car.year, fuel: s.car.fuel, mileage_km: null },
    symptom: { description: s.symptom, context: s.context, missing_info: [] },
  };
}

function startRun(s, chatId, now) {
  const c = buildCase(s, chatId, now);
  return {
    state: { ...s, stage: 'running', case: c },
    messages: [{ text: "C'est noté. J'analyse le son, puis je consulte les manuels : cela peut prendre une à deux minutes…" }],
    run: { kind: 'audio', case: c, file_id: s.file_id },
  };
}

function askCheck(s) {
  const chk = s.pending[s.index];
  const n = `Vérification ${s.index + 1}/${s.pending.length} : `;
  const where = chk.citation && chk.citation.source ? `\n(source : ${chk.citation.source}${chk.citation.page ? ', p. ' + chk.citation.page : ''})` : '';
  if (chk.answer_type === 'choice' && (chk.choices || []).length) {
    return { text: n + chk.q + where, buttons: [...chk.choices.map((c, i) => [{ text: c, data: `a:${i}` }]), [{ text: 'Je ne sais pas', data: 'a:-1' }]] };
  }
  if (chk.answer_type === 'free_text') return { text: n + chk.q + where + '\n(répondez par un message)' };
  return { text: n + chk.q + where, buttons: [[{ text: 'Oui', data: 'a:oui' }, { text: 'Non', data: 'a:non' }, { text: 'Je ne sais pas', data: 'a:-1' }]] };
}

// record the answer to the current check, then ask the next one or launch pass 2
function answer(s, value, chatId, now) {
  const chk = s.pending[s.index];
  const answers = [...(s.answers || []), { check_id: chk.id, answer: value, round: s.case.round + 1 }];
  if (s.index + 1 < s.pending.length) {
    const nx = { ...s, answers, index: s.index + 1 };
    return { state: nx, messages: [askCheck(nx)], run: null };
  }
  const c = { ...s.case, round: s.case.round + 1, status: 'waiting_checks', diagnosis: s.diagnosis, check_answers: [...(s.case.check_answers || []), ...answers] };
  if (s.audio) c.audio = s.audio;
  return {
    state: { ...s, stage: 'running', case: c, answers: [] },
    messages: [{ text: 'Merci. Je mets à jour le diagnostic avec vos réponses…' }],
    run: { kind: 'answers', case: c },
  };
}

function handle(state, upd, now) {
  let s = state && state.stage ? state : fresh();
  const cb = upd.callback_query;
  const m = upd.message || (cb && cb.message);
  if (!m) return { state: s, messages: [], run: null };
  const chatId = m.chat.id;
  const text = !cb && typeof m.text === 'string' ? m.text.trim() : '';
  const data = cb ? cb.data || '' : '';
  const voice = !cb && (m.voice || m.audio || m.video_note);

  if (/^\/(annuler|cancel|reset)\b/.test(text)) return { state: fresh(), messages: [{ text: "D'accord, on recommence. Envoyez une note vocale du moteur." }], run: null };
  if (/^\/(start|help|aide)\b/.test(text)) return { state: fresh(), messages: [{ text: HELP }], run: null };

  if (voice) { // a new voice note always starts a new case
    const caption = (m.caption || '').trim();
    s = { stage: 'need_car', file_id: voice.file_id, caption };
    const car = parseCar(caption);
    if (car) { s.car = car; s.stage = 'need_symptom'; }
    return { state: s, messages: [{ text: car ? 'Note vocale reçue. Décrivez le problème en une phrase (ex : le démarreur ne tourne pas, j’entends un clic).' : 'Note vocale reçue. Quelle est la voiture ? (ex : Renault Clio 2015 diesel)' }], run: null };
  }

  switch (s.stage) {
    case 'need_car': {
      const car = parseCar(text);
      if (!car) return { state: s, messages: [{ text: "Je n'ai pas compris. Écrivez la marque, le modèle et l'année (ex : Renault Clio 2015 diesel)." }], run: null };
      return { state: { ...s, car, stage: 'need_symptom' }, messages: [{ text: 'Décrivez le problème en une phrase (ex : le démarreur ne tourne pas, j’entends un clic).' }], run: null };
    }
    case 'need_symptom': {
      if (!text) return { state: s, messages: [{ text: 'Décrivez le problème par un message texte.' }], run: null };
      const ctx = guessContext(text);
      const nx = { ...s, symptom: text };
      if (ctx) return startRun({ ...nx, context: ctx }, chatId, now);
      return { state: { ...nx, stage: 'need_context' }, messages: [{ text: 'Quand le bruit apparaît-il ?', buttons: ctxButtons() }], run: null };
    }
    case 'need_context': {
      const k = data.startsWith('ctx:') ? data.slice(4) : null;
      if (!k || !CONTEXTS[k]) return { state: s, messages: [{ text: 'Choisissez une option :', buttons: ctxButtons() }], run: null };
      return startRun({ ...s, context: k }, chatId, now);
    }
    case 'answering': {
      const chk = s.pending[s.index];
      let value = null;
      if (data.startsWith('a:')) {
        const v = data.slice(2);
        value = v === '-1' ? 'je ne sais pas' : (chk.answer_type === 'choice' ? (chk.choices || [])[parseInt(v, 10)] : v);
      } else if (text && chk.answer_type === 'free_text') value = text;
      if (!value) return { state: s, messages: [askCheck(s)], run: null };
      return answer(s, value, chatId, now);
    }
    case 'running':
      return { state: s, messages: [{ text: "Analyse en cours, un instant…" }], run: null };
    default:
      return { state: fresh(), messages: [{ text: HELP }], run: null };
  }
}

const pct = (p) => Math.round(p * 100) + ' %';

function guideMessages(a3) {
  const g = a3 && a3.guide;
  if (!g) return [{ text: "Je n'ai pas pu préparer le guide de réparation (service indisponible). Consultez un mécanicien." }];
  const out = [];
  const warn = (g.safety_warnings || []).map((w) => '⚠️ ' + w).join('\n');
  if (g.status === 'guide_available' || (g.repair_guide && (g.repair_guide.steps || []).length)) {
    const steps = (g.repair_guide.steps || []).map((x) => `${x.number}. ${x.action}  (source : ${x.source_id}${x.page ? ', p. ' + x.page : ''})`).join('\n');
    out.push({ text: `🔧 Guide de réparation (${(g.fault && g.fault.name) || 'panne'}) :\n${steps}` + (warn ? '\n\n' + warn : '') });
  } else {
    out.push({ text: `🔧 L'assistant de réparation recommande de consulter un professionnel (${g.status}).` + (warn ? '\n\n' + warn : '') });
  }
  const lim = (g.limitations || []).map((l) => '• ' + l).join('\n');
  if (lim) out.push({ text: 'Limites :\n' + lim });
  return out;
}

function format(state, kind, reply, labelsFr) {
  const s = state;
  const done = (messages) => ({ state: fresh(), messages, run: null });
  if (!reply || reply.error || reply.message === 'Error in workflow') {
    return done([{ text: "Désolé, le service n'a pas pu répondre (modèle indisponible ou quota atteint). Réessayez dans quelques minutes avec /annuler puis une nouvelle note vocale." }]);
  }
  if (reply.needs_new_audio) return done([{ text: reply.message || 'Enregistrement inutilisable : refaites un enregistrement de 5 à 10 secondes près du moteur, sans parler.' }]);
  if (reply.insufficient) {
    return done([{ text: "Je n'ai pas trouvé d'information fiable dans les manuels pour ce problème. Je préfère ne pas inventer : consultez un mécanicien." }]);
  }
  const a1 = reply.agent1 || null, a2 = reply.agent2 || reply, a3 = reply.agent3 || null;
  const msgs = [];
  if (a1 && a1.status === 'ok') {
    const fr = (labelsFr || {})[a1.label] || a1.label;
    msgs.push({ text: `🎧 Analyse du son : ${fr} (confiance ${a1.confidence}, bruit ${a1.anomaly_score >= 0.8 ? 'inhabituel' : 'dans la normale'}).` });
  } else if (kind === 'audio') {
    msgs.push({ text: "🎧 Je n'ai pas pu analyser le son : je me base sur votre description seule." });
  }
  if (a2.safety_stop) msgs.push({ text: '⛔ Ne roulez pas avec ce véhicule avant vérification (freins, direction ou surchauffe possibles).' });
  const causes = (a2.causes || []).map((c) => `• ${c.name} — ${pct(c.prob)}`).join('\n');
  msgs.push({ text: 'Causes probables :\n' + causes + (a2.message_to_user ? '\n\n' + a2.message_to_user : '') });
  const round = (s.case && s.case.round) || 0;
  if ((a2.checks || []).length && !a2.conclusive && round < MAX_ROUNDS) {
    const ns = { ...s, stage: 'answering', pending: a2.checks, index: 0, answers: [], diagnosis: [...((s.case && s.case.diagnosis) || []), a2], audio: a1 && a1.status === 'ok' ? a1 : s.audio || null };
    msgs.push({ text: 'Pour confirmer, quelques vérifications (moteur coupé, frein à main serré) :' }, askCheck(ns));
    return { state: ns, messages: msgs, run: null };
  }
  if (!a2.conclusive) msgs.push({ text: 'Pas de conclusion nette après les vérifications : consultez un mécanicien pour confirmer.' });
  return { state: fresh(), messages: [...msgs, ...guideMessages(a3)], run: null };
}
