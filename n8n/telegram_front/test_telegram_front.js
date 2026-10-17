// Tests of the Telegram front logic (no Telegram, no n8n, no agents): node n8n/telegram_front/test_telegram_front.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, 'telegram_front.js'), 'utf8');
const api = vm.runInNewContext(src + '\n;({ parseCar, guessContext, handle, format, fresh })', {});
const J = (x) => JSON.parse(JSON.stringify(x)); // cross-realm objects
const { parseCar, guessContext, handle, format } = api;
const msg = (o) => ({ message: { chat: { id: 7 }, ...o } });
const press = (data) => ({ callback_query: { data, message: { chat: { id: 7 } } } });
const NOW = 1760000000000;

// --- parsing
assert.deepStrictEqual(J(parseCar('Renault Clio 2015 diesel')), { make: 'Renault', model: 'Clio', year: 2015, fuel: 'diesel' });
assert.deepStrictEqual(J(parseCar('peugeot 208 gt line 2019 essence')), { make: 'Peugeot', model: '208 gt line', year: 2019, fuel: 'petrol' });
assert.strictEqual(parseCar('ma voiture'), null);
assert.strictEqual(parseCar('Clio'), null);
assert.strictEqual(guessContext("le démarreur ne tourne pas, j'entends un clic"), 'startup');
assert.strictEqual(guessContext('ça siffle'), null);

// --- commands
let r = J(handle({ stage: 'answering' }, msg({ text: '/annuler' }), NOW));
assert.strictEqual(r.state.stage, 'idle');
r = J(handle(undefined, msg({ text: 'bonjour' }), NOW));
assert(r.messages[0].text.includes('note vocale') && r.state.stage === 'idle');

// --- voice note -> car -> symptom (context deduced) -> pass 1
r = J(handle(r.state, msg({ voice: { file_id: 'F1' } }), NOW));
assert.strictEqual(r.state.stage, 'need_car'); assert.strictEqual(r.state.file_id, 'F1');
r = J(handle(r.state, msg({ text: 'ma voiture' }), NOW));
assert.strictEqual(r.state.stage, 'need_car'); assert(r.messages[0].text.includes('Renault Clio'));
r = J(handle(r.state, msg({ text: 'Renault Clio 2015 diesel' }), NOW));
assert.strictEqual(r.state.stage, 'need_symptom');
r = J(handle(r.state, msg({ text: "le démarreur ne tourne pas, j'entends un clic" }), NOW));
assert.strictEqual(r.state.stage, 'running'); assert.strictEqual(r.run.kind, 'audio'); assert.strictEqual(r.run.file_id, 'F1');
const c1 = r.run.case;
assert.strictEqual(c1.round, 0); assert.strictEqual(c1.vehicle.model, 'Clio'); assert.strictEqual(c1.symptom.context, 'startup'); assert.strictEqual(c1.chat_id, '7');
assert.strictEqual(c1.audio, undefined); // Agent 1 fills it
const running = r.state;
assert(J(handle(running, msg({ text: 'alors ?' }), NOW)).messages[0].text.includes('en cours'));

// --- context asked with buttons when the text does not say it
let q = J(handle(J(handle(undefined, msg({ voice: { file_id: 'F2' }, caption: 'Peugeot 208 2016' }), NOW)).state, msg({ text: 'ça siffle' }), NOW));
assert.strictEqual(q.state.stage, 'need_context'); assert.strictEqual(q.messages[0].buttons[0].length, 4);
q = J(handle(q.state, press('ctx:idle'), NOW));
assert.strictEqual(q.run.case.symptom.context, 'idle'); assert.strictEqual(q.run.case.vehicle.make, 'Peugeot');

// --- agents answer pass 1: checks asked one by one
const A1 = { status: 'ok', label: 'dead_battery', confidence: 'medium', anomaly_score: 0.88, top3: [] };
const CIT = { source: 'clio.pdf', page: 225 };
const A2 = { pass: 1, conclusive: false, safety_stop: false, message_to_user: 'Vérifiez la batterie (p. 225).',
  causes: [{ name: 'Batterie déchargée', prob: 0.7 }, { name: 'Cosses desserrées', prob: 0.3 }],
  checks: [{ id: 'c1', q: 'La batterie est-elle neuve ?', answer_type: 'yes_no', citation: CIT },
           { id: 'c2', q: 'Le bruit change ?', answer_type: 'choice', choices: ['augmente', 'diminue'], citation: CIT },
           { id: 'c3', q: 'Depuis quand ?', answer_type: 'free_text', citation: CIT }] };
r = J(format(running, 'audio', { agent1: A1, agent2: A2, agent3: null }, { dead_battery: 'Batterie faible' }));
assert.strictEqual(r.state.stage, 'answering');
assert(r.messages[0].text.includes('Batterie faible'));
assert(r.messages.some((m) => m.text.includes('Batterie déchargée — 70 %')));
const q1 = r.messages[r.messages.length - 1];
assert(q1.text.includes('Vérification 1/3') && q1.buttons[0].length === 3);
r = J(handle(r.state, msg({ text: 'oui' }), NOW)); // text for a button question: asked again
assert(r.messages[0].text.includes('Vérification 1/3'));
r = J(handle(r.state, press('a:oui'), NOW));
assert(r.messages[0].text.includes('Vérification 2/3') && r.messages[0].buttons.length === 3);
r = J(handle(r.state, press('a:1'), NOW)); // second option = "diminue"
assert(r.messages[0].text.includes('Vérification 3/3') && !r.messages[0].buttons);
r = J(handle(r.state, msg({ text: 'ce matin' }), NOW));
assert.strictEqual(r.run.kind, 'answers'); assert.strictEqual(r.state.stage, 'running');
const c2 = r.run.case;
assert.strictEqual(c2.round, 1); assert.strictEqual(c2.status, 'waiting_checks');
assert.deepStrictEqual(c2.check_answers.map((a) => [a.check_id, a.answer, a.round]), [['c1', 'oui', 1], ['c2', 'diminue', 1], ['c3', 'ce matin', 1]]);
assert.strictEqual(c2.diagnosis[0].pass, 1); assert.strictEqual(c2.audio.label, 'dead_battery'); // Agent 1 result goes back with pass 2

// --- final answer: Agent 2 conclusive + Agent 3 guide
const A2b = { pass: 2, conclusive: true, safety_stop: false, message_to_user: 'Rechargez ou remplacez la batterie.', checks: [], causes: [{ name: 'Batterie déchargée', prob: 0.9 }] };
const A3 = { guide: { status: 'guide_available', fault: { name: 'Batterie' }, safety_warnings: ['Coupez le contact'],
  repair_guide: { steps: [{ number: 1, action: 'Débrancher la borne négative', source_id: 'ifixit_1', page: 3 }] }, limitations: ['guide générique'] } };
r = J(format(J(handle(r.state, msg({ text: 'x' }), NOW)).state, 'answers', { agent1: null, agent2: A2b, agent3: A3 }, {}));
assert.strictEqual(r.state.stage, 'idle');
const all = r.messages.map((m) => m.text).join('\n');
assert(all.includes('Débrancher la borne négative') && all.includes('Coupez le contact') && all.includes('guide générique'));

// --- failures end cleanly
for (const [reply, expect] of [[null, 'pas pu répondre'], [{ error: 'x' }, 'pas pu répondre'], [{ needs_new_audio: true, message: 'refaites un enregistrement' }, 'refaites'],
                               [{ insufficient: true, reason: 'x' }, 'mécanicien']]) {
  const f = J(format(running, 'audio', reply, {}));
  assert.strictEqual(f.state.stage, 'idle'); assert(f.messages[0].text.includes(expect), JSON.stringify(f.messages));
}
// safety stop and "no clear conclusion" without checks left
const f2 = J(format({ ...running, case: { round: 2 } }, 'answers', { agent2: { ...A2b, conclusive: false, safety_stop: true }, agent3: null }, {}));
assert(f2.messages.some((m) => m.text.includes('Ne roulez pas')) && f2.messages.some((m) => m.text.includes('mécanicien')));
console.log('telegram front: all tests passed');
