// Telegram front for the Car Sound Doctor chain. Owner: B2. Pure functions (no I/O): the Code nodes of
// n8n/workflows/telegram_front.json embed this file (built by n8n/telegram_front/build_telegram_front.py) and do the HTTP, file and LLM work.
// Test: node n8n/telegram_front/test_telegram_front.js
//
// One conversation per chat, stored by the workflow. Stages:
//   idle -> need_car -> need_symptom -> need_context -> (agents, pass 1) -> answering -> (agents, pass 2) -> idle
// handle(state, upd)         -> { state, messages, run }   upd = a Telegram update (message or callback_query), upd.p1 = prompt P1's extraction of the text (or absent)
// format(state, kind, reply) -> { state, messages, run, summary_data }   reply = answer of the n8n chain (kind: 'audio' | 'answers')
// A message is { text, buttons?: [[{ text, data }]], ask?: { missing, have } }; `ask` = a question the P2 prompt may re-word (the text is the fixed fallback).
// `run` is null or { kind: 'audio'|'answers', case, file_id? }. LLM prompts (prompts/P1, P2, P7) are called by the nodes; this file holds their guardrails.

const CONTEXTS = { idle: 'Au ralenti', startup: 'Au démarrage', braking: 'Au freinage', driving: 'En roulant' };
const MAX_ROUNDS = 2;
const HELP =
  "Bonjour ! Je suis Car Sound Doctor.\nEnvoyez-moi une note vocale de 5 à 10 secondes près du moteur (sans parler), " +
  'je vous demanderai la voiture, le problème et quand le bruit apparaît, puis je vous poserai quelques vérifications simples.\n' +
  '/annuler pour recommencer.';
const ASK_SYMPTOM = 'Décrivez le problème en une phrase (ex : le démarreur ne tourne pas, j’entends un clic).';

function fresh() { return { stage: 'idle' }; }

// "Renault Clio 2015 diesel" -> { make, model, year, fuel } | null   (fallback when prompt P1 is unavailable)
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

// the driver's words already say when the noise happens (fallback when prompt P1 is unavailable)
function guessContext(text) {
  const t = String(text || '').toLowerCase();
  if (/(d[ée]marr|d[ée]marrage|au d[ée]marrage|clic|ne d[ée]marre)/.test(t)) return 'startup';
  if (/(ralenti|[àa] l.arr[êe]t|moteur tourne)/.test(t)) return 'idle';
  if (/(frein|freinage)/.test(t)) return 'braking';
  if (/(en roulant|[àa] \d+ ?km|sur la route|acc[ée]l[ée]r)/.test(t)) return 'driving';
  return null;
}

// prompt P1's answer, made safe: only known fields, right types
const FUELS = ['petrol', 'diesel', 'hybrid', 'electric'];
function cleanP1(p) {
  if (!p || typeof p !== 'object') return null;
  const s = (x) => (typeof x === 'string' && x.trim() && x.trim().length < 60 ? x.trim() : null);
  const year = parseInt(p.year, 10);
  return {
    make: s(p.make), model: s(p.model), year: year >= 1980 && year <= 2049 ? year : null,
    fuel: FUELS.includes(p.fuel) ? p.fuel : null, symptom: s(p.symptom), manual_symptom: s(p.manual_symptom),
    context: Object.keys(CONTEXTS).includes(p.context) ? p.context : 'unknown',
  };
}

const ctxButtons = () => [Object.keys(CONTEXTS).map((k) => ({ text: CONTEXTS[k], data: 'ctx:' + k }))];

function buildCase(s, chatId, now) {
  return {
    case_id: 'tg_' + chatId + '_' + now, chat_id: String(chatId), status: 'collecting_info', round: 0,
    created_at: new Date(now).toISOString(),
    user_input: { text: s.said || s.symptom, voice_transcript: null, language: 'fr', audio_file_id: s.file_id },
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

// what the agents (and the search) receive as the problem: P1's manual wording first, then the driver's short wording, else the whole text.
// Measured (prompts/P1_extraction.md): searching with both finds the right manual page for 8 of 8 lay messages, the raw text for 4 of 8.
function symptomOf(text, p1) {
  const parts = p1 ? [p1.manual_symptom, p1.symptom].filter(Boolean) : [];
  return parts.length ? [...new Set(parts)].join('. ') : text;
}

// car information from this message (prompt P1, else the regex parser) merged with what was already understood
function carFrom(text, p1, partial) {
  const base = partial || {};
  let got = {};
  if (p1) got = { make: p1.make, model: p1.model, year: p1.year, fuel: p1.fuel };
  else { const c = parseCar(text); if (c) got = c; }
  const year = got.year || base.year || (String(text).match(/\b(19[89]\d|20[0-4]\d)\b/) || [])[1];
  return { make: got.make || base.make || null, model: got.model || base.model || null, year: year ? parseInt(year, 10) : null, fuel: got.fuel || base.fuel || null };
}

function askCar(s, car) {
  const have = Object.fromEntries(Object.entries({ make: car.make, model: car.model, year: car.year }).filter(([, v]) => v));
  let missing, fixed;
  if (!car.make && !car.model) { missing = "la marque, le modèle et l'année du véhicule"; fixed = "Je n'ai pas compris la voiture. Écrivez la marque, le modèle et l'année (ex : Renault Clio 2015 diesel)."; }
  else if (!car.model) { missing = 'le modèle du véhicule'; fixed = `Quel est le modèle de votre ${car.make} ?`; }
  else { missing = "l'année du véhicule"; fixed = `De quelle année est votre ${car.make} ${car.model} ?`; }
  return { state: { ...s, car_partial: car }, messages: [{ text: fixed, ask: { missing, have } }], run: null };
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
  const p1 = cleanP1(upd.p1);  // prompt P1's reading of the text or caption (absent when the LLM is off or failed)

  if (/^\/(annuler|cancel|reset)\b/.test(text)) return { state: fresh(), messages: [{ text: "D'accord, on recommence. Envoyez une note vocale du moteur." }], run: null };
  if (/^\/(start|help|aide)\b/.test(text)) return { state: fresh(), messages: [{ text: HELP }], run: null };

  if (voice) { // a new voice note always starts a new case
    const caption = (m.caption || '').trim();
    s = { stage: 'need_car', file_id: voice.file_id, caption };
    const car = caption ? carFrom(caption, p1, null) : null;
    if (car && car.make && car.model && car.year) { s.car = car; s.stage = 'need_symptom'; }
    const msg = s.car ? { text: 'Note vocale reçue. ' + ASK_SYMPTOM }
      : { text: 'Note vocale reçue. Quelle est la voiture ? (ex : Renault Clio 2015 diesel)', ask: { missing: "la marque, le modèle et l'année du véhicule", have: {} } };
    return { state: s, messages: [msg], run: null };
  }

  switch (s.stage) {
    case 'need_car': {
      const car = carFrom(text, p1, s.car_partial);
      if (!(car.make && car.model && car.year)) return askCar(s, car);
      const nx = { ...s, car, car_partial: undefined, stage: 'need_symptom' };
      // the driver may have said everything in one message: use the symptom and the moment when P1 found them
      if (p1 && p1.symptom) {
        const ctx = p1.context !== 'unknown' ? p1.context : guessContext(text);
        const full = { ...nx, symptom: symptomOf(text, p1), said: text };
        if (ctx) return startRun({ ...full, context: ctx }, chatId, now);
        return { state: { ...full, stage: 'need_context' }, messages: [{ text: 'Quand le bruit apparaît-il ?', buttons: ctxButtons() }], run: null };
      }
      return { state: nx, messages: [{ text: ASK_SYMPTOM }], run: null };
    }
    case 'need_symptom': {
      if (!text) return { state: s, messages: [{ text: 'Décrivez le problème par un message texte.', ask: { missing: 'la description du problème (que se passe-t-il ?)', have: s.car } }], run: null };
      const ctx = p1 && p1.context !== 'unknown' ? p1.context : guessContext(text);
      const nx = { ...s, symptom: symptomOf(text, p1), said: text };
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
      if (!value) return { state: s, messages: [{ ...askCheck(s), ...(text ? { ask: { missing: 'une réponse claire : oui, non ou « je ne sais pas »', have: { question: chk.q } } } : {}) }], run: null };
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

// ---- prompt P7 (short final message, read aloud): its input, its guardrails and a deterministic fallback
function summaryData(a1, a2, a3, labelsFr) {
  const top = (a2.causes || [])[0];
  const g = a3 && a3.guide;
  const advice = String(a2.message_to_user || '').replace(/\s+/g, ' ').split(/(?<=[.!?])\s/).slice(0, 2).join(' ').slice(0, 300);
  return {
    cause: top ? top.name : null, probabilite_pct: top ? Math.round(top.prob * 100) : null, conseil: advice || null,
    ne_pas_rouler: !!a2.safety_stop, guide: g ? g.status : 'aucun', son: a1 && a1.status === 'ok' ? ((labelsFr || {})[a1.label] || a1.label) : null,
    diagnostic_ferme: !!a2.conclusive,
  };
}

// the rules of the P7 evaluation (prompts/eval/run_p2_p7.py check_p7): a summary that breaks one is not sent
function checkSummary(text, data) {
  const problems = [];
  const t = String(text || '');
  if (!t.trim()) return ['empty'];
  if (t.length > 450) problems.push('too long');
  if (/[*#_`•]|[\u{1F300}-\u{1FAFF}☀-➿]/u.test(t)) problems.push('markdown or emoji');
  if (/\b(please|the|your|what|which|could you|car)\b/i.test(t)) problems.push('not French');
  const allowed = new Set((JSON.stringify(data).match(/\d+/g) || []));
  if ((t.match(/\d+/g) || []).some((n) => !allowed.has(n))) problems.push('invented numbers');
  if (data.ne_pas_rouler && !/ne roulez pas|ne conduisez pas|n.utilisez pas|arr[êe]tez|immobilis/i.test(t)) problems.push('does not say to stop driving');
  const pro = data.guide === 'professional_assistance_required' || data.diagnostic_ferme === false || !data.cause;
  if (pro && !/m[ée]canicien|professionnel|garag/i.test(t)) problems.push('does not send to a professional');
  if (/continu(ez|er) [àa] rouler|vous pouvez (continuer|rouler|conduire)|pas de danger|sans risque/i.test(t)) problems.push('invents permission to drive');
  return problems;
}

function templateSummary(data) {
  const parts = [];
  if (data.cause) parts.push(`La cause la plus probable est ${data.cause}${data.probabilite_pct ? `, à ${data.probabilite_pct} %` : ''}.`);
  else parts.push('Les manuels fournis ne permettent pas de conclure.');
  if (data.conseil) parts.push(data.conseil);
  if (data.ne_pas_rouler) parts.push('Ne roulez pas avec le véhicule.');
  if (data.guide === 'professional_assistance_required' || data.diagnostic_ferme === false || !data.cause) parts.push('Consultez un mécanicien ou un professionnel.');
  return parts.join(' ');
}

// the text of a P2 question is accepted only if it is one short French question
function checkQuestion(text) {
  const t = String(text || '').trim();
  return t.split('?').length === 2 && t.endsWith('?') && t.split(/\s+/).length <= 25 && !/\b(please|the|your|what|which|could you|car)\b/i.test(t);
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
  return { state: fresh(), messages: [...msgs, ...guideMessages(a3)], run: null, summary_data: summaryData(a1, a2, a3, labelsFr) };
}
