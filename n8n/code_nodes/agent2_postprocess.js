// Agent 2, node "Ground and decide" (Code node source). Owner: B2. Port of agent2_diagnosis/agent2.py
// (build_causes, build_checks, is_conclusive, safety_stop). Wrapped by agent2_diagnosis/build_workflow.py.
// Test: node n8n/code_nodes/test_agent2_nodes.js
//
// postprocess(ctx, agentOut, LABELS)
//   ctx      : ctx from "Prepare input" ({n, allow_new, asked, language})
//   agentOut : output of the AI Agent node: {output: "<LLM text>", intermediateSteps: [{observation}]}
// returns    : item for validate_output.js: {agent, output (agent2_output or null), retrieved, insufficient, reason}
// Rules: only passages that /search returned count; uncited causes and checks are dropped; conclusive, safety_stop,
// label validity and check ids are decided here, not by the LLM.

function postprocess(ctx, agentOut, LABELS) {
  const LEVELS = ['exact_model', 'make', 'generic', 'none'];
  const SAFETY = /frein|brake|direction|steering|surchauffe|overheat|temp[ée]rature|oil pressure|pression d.huile/i;
  const CONCLUSIVE_PROB = 0.6, CONCLUSIVE_GAP = 0.2;
  const DEFAULT_MESSAGE = { fr: 'Pour confirmer, répondez à ces questions :', en: 'To confirm, please answer these questions:' };
  // LLMs add spaces, brackets or quotes around ids: ' [clio4ph1fr_p225_c1]' -> 'clio4ph1fr_p225_c1'
  const cleanId = (x) => String(x == null ? '' : x).replace(/^[\s\[('"]+|[\s\])'",.;]+$/g, '');
  // small models sometimes answer with a machine id ('dead_battery') as the cause name: show the label's name instead
  const labelNames = Object.fromEntries(LABELS.labels.map((l) => [l.id, l]));
  const readable = (name, label) => {
    if (!(name in labelNames) && !/^[A-Za-z0-9]+(_[A-Za-z0-9]+)+$/.test(name)) return name; // exactly a label id, or looks like an id
    const l = labelNames[name] || labelNames[label];
    return l ? l[ctx.language] || l.en : name.replace(/_/g, ' ');
  };
  const known = new Set([...LABELS.labels.map((l) => l.id), ...LABELS.hypotheses_only.ids]);

  // 1. the passages the agent really retrieved (tool results)
  const byId = {};
  let level = 'exact_model';
  for (const step of agentOut.intermediateSteps || []) {
    let obs = step.observation;
    if (typeof obs === 'string') {
      try { obs = JSON.parse(obs); } catch (e) { continue; }
    }
    if (!obs || !Array.isArray(obs.passages)) continue;
    for (const p of obs.passages) byId[p.chunk_id] = p;
    if (LEVELS.indexOf(obs.fallback_level) > LEVELS.indexOf(level)) level = obs.fallback_level;
  }
  const retrieved = Object.keys(byId);
  const fail = (reason) => ({ agent: 'agent2_output', output: null, retrieved, insufficient: true, reason });
  if (!retrieved.length) return fail('no manual passages retrieved');

  // 2. the LLM answer
  let raw = agentOut.output;
  if (typeof raw === 'string') {
    const m = raw.match(/\{[\s\S]*\}/);
    try { raw = m ? JSON.parse(m[0]) : {}; } catch (e) { raw = {}; }
  }
  raw = raw || {};

  // 3. grounded causes
  let causes = [];
  for (const c of raw.causes || []) {
    const ids = (c.sources || []).map(cleanId).filter((s) => byId[s]);
    if (!ids.length || !c.name) continue;
    let prob = Math.max(Number(c.prob) || 0, 0);
    if (String(c.status || '').toLowerCase() === 'contradicted') prob = Math.min(prob, 0.05); // P5: an answer shows it is not the cause
    const label = known.has(c.label) ? c.label : null;
    causes.push({
      name: readable(String(c.name).trim(), label),
      label,
      prob,
      evidence: (c.evidence || []).map(String).concat(ids.map((i) => 'passage ' + i)),
    });
  }
  causes = causes.sort((a, b) => b.prob - a.prob).slice(0, 5);
  if (!causes.length) return fail('the passages support no cause (or the model cited none)');
  const total = causes.reduce((s, c) => s + c.prob, 0) || 1;
  causes.forEach((c) => { c.prob = Math.round((c.prob / total) * 1000) / 1000; });

  // 4. grounded checks
  let checks = [];
  if (ctx.allow_new) {
    for (const c of raw.checks || []) {
      const p = byId[cleanId(c.source)];
      if (!p || !c.q || checks.length >= 5) continue;
      let kind = ['yes_no', 'choice', 'free_text'].includes(c.answer_type) ? c.answer_type : 'yes_no';
      const choices = (c.choices || []).map(String);
      if (kind === 'choice' && !choices.length) kind = 'free_text';
      const chk = {
        id: 'c' + (ctx.asked + checks.length + 1),
        q: c.q,
        why: c.why || '',
        answer_type: kind,
        citation: { chunk_id: p.chunk_id, source: p.source, page: p.page === undefined ? null : p.page, verified: true },
      };
      if (kind === 'choice') chk.choices = choices;
      checks.push(chk);
    }
  }

  // 5. decisions
  const second = causes.length > 1 ? causes[1].prob : 0;
  const isConclusive = causes[0].prob >= CONCLUSIVE_PROB && causes[0].prob - second >= CONCLUSIVE_GAP;
  const conclusive = ctx.n === 1 && checks.length ? false : isConclusive;
  if (conclusive) checks = [];
  const top = causes[0];
  const message = typeof raw.message_to_user === 'string' ? raw.message_to_user : '';
  const output = {
    pass: ctx.n,
    causes,
    checks,
    conclusive,
    message_to_user: message || (checks.length ? DEFAULT_MESSAGE[ctx.language] || DEFAULT_MESSAGE.en : ''),
    // most likely cause decides; less likely risky causes are covered by the caution sentence the prompt asks for
    safety_stop: SAFETY.test(top.name + ' ' + (top.label || '')) || top.label === 'worn_brakes',
  };
  return { agent: 'agent2_output', output, retrieved, insufficient: false, reason: null, fallback_level: level };
}

// supportFilter(agentOut, search, filters, top)  [async, run just before postprocess]
// A citation that exists is not a citation that supports: for the `top` likeliest causes, search the cause's own name
// (the service reranks and drops weak passages) and keep the cause only if one of its cited passages comes back.
// Port of supported_causes() in agent2_diagnosis/agent2.py. Returns agentOut with `output` replaced by the filtered answer.
async function supportFilter(agentOut, search, filters, top) {
  const cleanId = (x) => String(x == null ? '' : x).replace(/^[\s\[('"]+|[\s\])'",.;]+$/g, '');
  const have = new Set();
  for (const step of agentOut.intermediateSteps || []) {
    let obs = step.observation;
    if (typeof obs === 'string') {
      try { obs = JSON.parse(obs); } catch (e) { continue; }
    }
    for (const p of (obs && obs.passages) || []) have.add(p.chunk_id);
  }
  let raw = agentOut.output;
  if (typeof raw === 'string') {
    const m = raw.match(/\{[\s\S]*\}/);
    try { raw = m ? JSON.parse(m[0]) : null; } catch (e) { raw = null; }
  }
  if (!raw || !Array.isArray(raw.causes)) return agentOut;
  const ranked = [...raw.causes].sort((a, b) => (Number(b.prob) || 0) - (Number(a.prob) || 0));
  const keep = [];
  for (let n = 0; n < ranked.length; n++) {
    const c = ranked[n];
    const ids = (c.sources || []).map(cleanId).filter((i) => have.has(i));
    if (n < (top || 3) && ids.length && c.name) {
      const res = await search(String(c.name), filters, 8);
      const back = new Set((res.passages || []).map((p) => p.chunk_id));
      if (!ids.some((i) => back.has(i))) continue; // cited passages do not talk about this cause
    }
    keep.push(c);
  }
  return { ...agentOut, output: { ...raw, causes: keep } };
}
