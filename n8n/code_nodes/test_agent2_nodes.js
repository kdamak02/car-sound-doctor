// Tests of the Agent 2 n8n Code nodes (agent2_prepare.js, agent2_postprocess.js). No n8n, no network, no LLM.
// Mirrors agent2_diagnosis/tests/test_agent2.py so the JavaScript and Python ports stay in sync.
// Run from the repo root:  node n8n/code_nodes/test_agent2_nodes.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
// results are JSON round-tripped: objects built inside the vm sandbox fail deepStrictEqual (other Array prototype)
const load = (file, name) => {
  const fn = vm.runInNewContext(read(file) + `\n;${name}`, {});
  return (...args) => JSON.parse(JSON.stringify(fn(...args)));
};
const prepare = load('n8n/code_nodes/agent2_prepare.js', 'prepare');
const postprocess = load('n8n/code_nodes/agent2_postprocess.js', 'postprocess');
// retrieve() is async: no JSON round trip here (a Promise cannot be serialised); its result is round-tripped where compared
const rescueFn = vm.runInNewContext(read('n8n/code_nodes/agent2_rescue.js') + '\n;rescue', {});
const supportFn = vm.runInNewContext(read('n8n/code_nodes/agent2_postprocess.js') + '\n;supportFilter', {});
const retrieveFn = vm.runInNewContext(read('n8n/code_nodes/agent2_retrieve.js') + '\n;retrieve', {});

const promptText = (stem) => {
  const f = fs.readdirSync(path.join(ROOT, 'prompts')).find((x) => x.startsWith(stem + '_'));
  const md = read('prompts/' + f).split('\r\n').join('\n'); // Windows checkouts have CRLF
  return md.match(/## Prompt \(current version\)\s*```text\n([\s\S]*?)```/)[1].trim();
};
const P3 = promptText('P3');
const SYSTEM = { 1: promptText('P4'), 2: promptText('P5') };
const LABELS = JSON.parse(read('contracts/labels.json'));
if (!LABELS.hypotheses_only) {  // labels.json 0.2: every label is listed; the trained ones are the classes of Agent 1's model (same rule as agent2_diagnosis/labels.py)
  const trained = new Set(JSON.parse(read('agent1_audio/results/ast.json')).labels);
  LABELS.hypotheses_only = { ids: LABELS.labels.map((l) => l.id).filter((i) => !trained.has(i)) };
}
const SEARCH = JSON.parse(read('contracts/examples/rag_search_response.json'));
const [P1, P2, PO] = SEARCH.passages.map((p) => p.chunk_id);

const freshCase = (round = 0) => {
  const c = JSON.parse(read('contracts/examples/case_record.json'));
  delete c.diagnosis; delete c.check_answers; delete c.repair;
  c.round = round;
  return c;
};
const agentOut = (llm, passages = SEARCH) => ({
  output: JSON.stringify(llm),
  intermediateSteps: [{ action: { tool: 'rag_search' }, observation: JSON.stringify(passages) }],
});
const run = (c, llm, passages) => {
  const p = prepare(c, P3, SYSTEM, LABELS);
  return postprocess(p.ctx, agentOut(llm, passages), LABELS);
};
const clone = (o) => JSON.parse(JSON.stringify(o));

const GOOD = {
  causes: [
    { name: 'Courroie usée', label: 'belt', prob: 7, evidence: ['audio: belt 0.71'], sources: [P1, 'not_a_chunk'] },
    { name: 'Direction assistée', label: 'power_steering', prob: 1, evidence: [], sources: [PO] },
    { name: 'Invented cause', label: 'belt', prob: 5, evidence: [], sources: ['not_a_chunk'] },
    { name: 'No citation', label: null, prob: 5, evidence: [], sources: [] },
  ],
  checks: [
    { q: 'Le bruit augmente-t-il en tournant le volant ?', why: 'w', answer_type: 'yes_no', source: PO },
    { q: 'Fissures sur la courroie ?', why: 'w', answer_type: 'yes_no', source: P2 },
    { q: 'Uncited check', why: 'w', answer_type: 'yes_no', source: 'not_a_chunk' },
    { q: 'Choix ?', why: 'w', answer_type: 'choice', choices: [], source: P1 },
  ],
  message_to_user: 'Merci ! Répondez :',
};

// the output must also pass the contract schema (checked in Python by contracts/validate.py; here: required fields)
const r1 = run(freshCase(), GOOD);
assert.strictEqual(r1.insufficient, false);
const o1 = r1.output;
assert.deepStrictEqual(o1.causes.map((c) => c.name), ['Courroie usée', 'Direction assistée']); // invented + uncited dropped
assert(Math.abs(o1.causes.reduce((s, c) => s + c.prob, 0) - 1) < 0.01);
assert.deepStrictEqual(o1.checks.map((c) => c.id), ['c1', 'c2', 'c3']);
assert.strictEqual(o1.checks[2].answer_type, 'free_text');
assert.strictEqual(o1.pass, 1); assert.strictEqual(o1.conclusive, false); assert.strictEqual(o1.safety_stop, false);
assert(o1.checks.every((c) => [P1, P2, PO].includes(c.citation.chunk_id)));
assert.deepStrictEqual(r1.retrieved.sort(), [P1, P2, PO].sort());

// sloppy ids (spaces, brackets) are cleaned
const sl = clone(GOOD); sl.causes[0].sources = [` [${P1}] `]; sl.checks[0].source = ` ${PO}`;
const slo = run(freshCase(), sl).output;
assert.strictEqual(slo.causes[0].name, 'Courroie usée'); assert.strictEqual(slo.checks[0].citation.chunk_id, PO);

// machine ids as cause names become readable (language of the case)
const mid = clone(GOOD); mid.causes[0].name = 'dead_battery'; mid.causes[0].label = 'dead_battery'; mid.causes[1].name = 'obdex_P2503'; mid.causes[1].label = null;
const mo = run(freshCase(), mid).output;
assert.strictEqual(mo.causes[0].name, 'Batterie faible'); assert.strictEqual(mo.causes[1].name, 'obdex P2503');
mid.causes[1].name = 'Surchauffe'; mid.causes[1].label = 'belt';
assert.strictEqual(run(freshCase(), mid).output.causes[1].name, 'Surchauffe'); // ordinary words stay

// P5 status: a contradicted cause is capped, the others are renormalised; `status` is not exposed
const st = { causes: [{ name: 'Courroie usée', label: 'belt', prob: 0.5, status: 'contradicted', evidence: ['non'], sources: [P1] },
                      { name: 'Autre', label: null, prob: 0.5, status: 'confirmed', evidence: [], sources: [PO] }], checks: [] };
const so = run(freshCase(), st).output;
assert(so.causes.find((c) => c.name === 'Courroie usée').prob <= 0.1); assert.strictEqual(so.causes[0].name, 'Autre');
assert(!('status' in so.causes[0]));

// unknown label -> null
const g = clone(GOOD); g.causes[0].label = 'flux_capacitor';
assert.strictEqual(run(freshCase(), g).output.causes[0].label, null);

// safety follows the most likely cause only
const s = clone(GOOD); s.causes[1] = { name: 'Plaquettes de frein usées', label: 'worn_brakes', prob: 4, evidence: [], sources: [PO] };
assert.strictEqual(run(freshCase(), s).output.safety_stop, false);
s.causes[1].prob = 9;
assert.strictEqual(run(freshCase(), s).output.safety_stop, true);

// pass 2: conclusive clears checks; unclear asks new checks numbered after c3; round cap 2 -> none
const answered = (round, firstOut) => {
  const c = freshCase(round);
  c.diagnosis = [firstOut];
  c.check_answers = [{ check_id: 'c1', answer: 'non', round: 1 }];
  return c;
};
const sure = { causes: [{ name: 'Courroie usée', label: 'belt', prob: 0.8, evidence: [], sources: [P1] },
                        { name: 'Autre', label: null, prob: 0.2, evidence: [], sources: [PO] }],
               checks: [{ q: 'q', why: 'w', answer_type: 'yes_no', source: P1 }] };
const o2 = run(answered(1, o1), sure).output;
assert.strictEqual(o2.pass, 2); assert.strictEqual(o2.conclusive, true); assert.deepStrictEqual(o2.checks, []);

const unclear = clone(GOOD); unclear.causes[0].prob = 5; unclear.causes[1].prob = 4; // 56% / 44%
const o3 = run(answered(1, o1), unclear).output;
assert.strictEqual(o3.conclusive, false); assert.strictEqual(o3.checks[0].id, 'c4');
const o4 = run(answered(2, o1), unclear).output;
assert.strictEqual(o4.pass, 3); assert.strictEqual(o4.conclusive, false); assert.deepStrictEqual(o4.checks, []);

// nothing grounded -> insufficient
assert.strictEqual(run(freshCase(), { causes: [], checks: [] }).insufficient, true);
assert.strictEqual(postprocess(prepare(freshCase(), P3, SYSTEM, LABELS).ctx, { output: 'not json', intermediateSteps: [] }, LABELS).insufficient, true);
const noPass = { ...SEARCH, passages: [], fallback_level: 'none' };
assert.strictEqual(run(freshCase(), GOOD, noPass).insufficient, true);

// the tool observation may be an object, not a string
const objObs = { output: JSON.stringify(GOOD), intermediateSteps: [{ observation: SEARCH }] };
assert.strictEqual(postprocess(prepare(freshCase(), P3, SYSTEM, LABELS).ctx, objObs, LABELS).insufficient, false);

// prepare: audio text, prompts per pass, filters, previous answers
const p1 = prepare(freshCase(), P3, SYSTEM, LABELS);
assert(p1.user_message.includes('Courroie qui siffle (probability 71 %') && p1.user_message.includes('Unusualness compared with healthy engines: high'));
assert(p1.system_prompt.includes('rank the 2 to 5 most likely causes'));
assert.deepStrictEqual(JSON.parse(p1.filters_json), { section: ['symptoms', 'causes', 'codes'], make: 'Peugeot', model: '208', year: 2020 });
const p2 = prepare(answered(1, o1), P3, SYSTEM, LABELS);
assert(p2.system_prompt.includes('update the probability'));
assert(p2.user_message.includes('c1:') && p2.user_message.includes('-> non') && p2.user_message.includes('New checks allowed: yes'));
assert.strictEqual(p2.ctx.asked, 3);
assert(prepare(answered(2, o1), P3, SYSTEM, LABELS).user_message.includes('New checks allowed: no'));
const bad = freshCase(); bad.audio = { status: 'unusable_audio', label: null };
assert(prepare(bad, P3, SYSTEM, LABELS).user_message.includes('No usable audio'));

// prefetch mode: queries, no tool sentence, retrieval in the workflow, then the same grounding
(async () => {
  const pp = prepare(freshCase(), P3, SYSTEM, LABELS, 'prefetch');
  assert(!pp.user_message.includes('rag_search'));
  assert(prepare(freshCase(), P3, SYSTEM, LABELS).user_message.includes('rag_search')); // tool mode keeps it
  assert.deepStrictEqual(pp.queries, ['sifflement au ralenti, surtout le matin Squealing belt', 'Squealing belt causes']);
  const calls = [];
  const search = async (q, f, k) => { calls.push([q, f, k]); return JSON.parse(JSON.stringify(SEARCH)); };
  const r = await retrieveFn({ ...pp, top_k: 3 }, search, 6);
  assert.strictEqual(calls.length, 2); assert.strictEqual(calls[0][2], 3);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(calls[0][1])), { section: ['symptoms', 'causes', 'codes'], make: 'Peugeot', model: '208', year: 2020 });
  assert(r.full_prompt.includes(`[${P1}]`) && r.full_prompt.includes('Retrieval level: exact_model') && r.full_prompt.includes('rank the 2 to 5 most likely causes'));
  const uniq = JSON.parse(JSON.stringify(r.observations))[0].observation.passages;
  assert.strictEqual(uniq.length, 3); // the same 3 passages came back for both queries: de-duplicated
  // the observations feed "Ground and decide" exactly like the tool results of the agent
  const out = postprocess(pp.ctx, { output: JSON.stringify(GOOD), intermediateSteps: JSON.parse(JSON.stringify(r.observations)) }, LABELS);
  assert.strictEqual(out.insufficient, false); assert.strictEqual(out.output.causes[0].name, 'Courroie usée');
  // nothing found -> empty context, guardrail then refuses
  const empty = await retrieveFn(pp, async () => ({ fallback_level: 'none', passages: [] }), 6);
  assert(empty.full_prompt.includes('(none found)'));
  assert.strictEqual(postprocess(pp.ctx, { output: JSON.stringify(GOOD), intermediateSteps: JSON.parse(JSON.stringify(empty.observations)) }, LABELS).insufficient, true);
    // reading guide: the decision table of Agent 1's integration guide (same cases as the Python test)
  const reading = (audio) => {
    const c = freshCase();
    c.audio = { status: 'ok', label: 'belt', anomaly_score: 0.9, confidence: 'high', top3: [{ label: 'belt', prob: 0.8 }], description: { summary: 's' }, ...audio };
    return prepare(c, P3, SYSTEM, LABELS).user_message.split('Reading guide: ')[1].split('\n')[0];
  };
  assert(reading({}).includes('agree'));
  assert(reading({ label: 'normal', anomaly_score: 0.5 }).startsWith('The engine probably sounds healthy'));
  assert(reading({ label: 'normal', anomaly_score: 0.97 }).includes('timing chain'));
  assert.strictEqual(reading({ label: 'normal', anomaly_score: 0.88 }), 'none');
  assert(reading({ confidence: 'low', anomaly_score: 0.5 }).includes('not conclusive'));
  assert.strictEqual(reading({ anomaly_score: 0.6 }), 'none');
  assert(prepare(freshCase(), P3, SYSTEM, LABELS).user_message.includes('Unusualness compared with healthy engines: high'));
  // rescue: the agent ended with an empty answer; its tool results still feed one extra JSON-mode call
  const rp = prepare(freshCase(), P3, SYSTEM, LABELS);  // tool mode: the message ends with the rag_search sentence
  const steps = [{ observation: JSON.stringify(SEARCH) }, { observation: SEARCH }, { observation: 'not json' }];
  const rs = JSON.parse(JSON.stringify(rescueFn(rp, steps, 6)));
  assert.strictEqual(rs.rescued_passages, 3);  // 3 distinct passages, duplicates and the unparsable step ignored
  assert(rs.full_prompt.includes(`[${P1}]`) && rs.full_prompt.includes('rank the 2 to 5 most likely causes'));
  assert(!rs.full_prompt.includes('Use the rag_search tool'));
  assert(rs.full_prompt.endsWith('Answer now with the JSON object only.'));
  assert(rescueFn(rp, [], 6).full_prompt.includes('(none found)'));
  // support check: a cause stays only if one of its cited passages comes back when its own name is searched
  const ans = { output: JSON.stringify({ causes: [{ name: 'belt', prob: 0.7, sources: ['a'] }, { name: 'battery', prob: 0.3, sources: ['b'] }] }),
    intermediateSteps: [{ observation: { passages: [{ chunk_id: 'a' }, { chunk_id: 'b' }] } }] };
  const sup = await supportFn(ans, async (q) => ({ passages: q === 'battery' ? [{ chunk_id: 'b' }] : [] }), {}, 3);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(sup.output.causes.map((c) => c.name))), ['battery']);
  console.log('agent2 n8n nodes: all tests passed');
})();
