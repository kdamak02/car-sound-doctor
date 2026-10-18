// Agent 2, node "Prepare input" (Code node source). Owner: B2. Port of agent2_diagnosis/agent2.py (describe_audio,
// user_message). The build script agent2_diagnosis/build_workflow.py wraps this function into the n8n Code node
// and embeds P3, P4, P5 (prompts/*.md) and contracts/labels.json. Test: node n8n/code_nodes/test_agent2_nodes.js
//
// prepare(c, P3, SYSTEM, LABELS)
//   c      : case record (contracts/case_record.schema.json); c.audio is the Agent 1 output
//   P3     : audio text template; SYSTEM : {1: P4 text, 2: P5 text}
// returns  : { case, ctx, system_prompt, user_message, filters_json }

function prepare(c, P3, SYSTEM, LABELS, mode) {  // mode: 'tool' (default) or 'prefetch' (passages are added later by "Retrieve passages")
  const MAX_ROUNDS = 2;
  const labelEn = Object.fromEntries(LABELS.labels.map((l) => [l.id, l.en]));
  const labelNames = Object.fromEntries(LABELS.labels.map((l) => [l.id, l]));
  const known = [...new Set([...Object.keys(labelEn), ...LABELS.hypotheses_only.ids])].sort();
  const n = (c.round || 0) + 1; // pass number = check rounds answered + 1
  const allowNew = n <= MAX_ROUNDS;
  const diagnosis = c.diagnosis || [];
  const lang = (c.user_input && c.user_input.language) || 'unknown';

  const a = c.audio;
  let audioText = "No usable audio: rely on the user's description only.";
  if (a && a.status === 'ok') {
    const top = a.top3 || [];
    const score = a.anomaly_score;
    const pct = (p) => Math.round(p * 100) + ' %';
    const name = (l) => (labelNames[l] ? labelNames[l][lang] || labelNames[l].en : l);
    // anomaly_score is a percentile against HEALTHY engines (0.5 = typical healthy sound), see Agent 1's INTEGRATION.md
    const unusualness = score == null ? 'unknown' : score >= 0.95 ? 'very high' : score >= 0.8 ? 'high' : 'normal range';
    // decision table of Agent 1's guide, computed here so a small model does not have to infer it
    const normal = String(a.label || '').startsWith('normal');
    const lines = [];
    if (a.confidence === 'low') lines.push("The audio is not conclusive: rely on the driver's description and on the checks more than on the sound.");
    if (score != null && !normal && score >= 0.8) lines.push('The sound class and the anomaly score agree: start from the most likely classes.');
    if (score != null && normal && score < 0.8) lines.push('The engine probably sounds healthy: ask whether the noise is intermittent and suggest recording again when it happens.');
    if (score != null && normal && score >= 0.95) {
      lines.push('Very unusual sound outside the classes the audio model knows: also consider these faults and separate them with checks: ' +
        LABELS.hypotheses_only.ids.map((i) => i.replace(/_/g, ' ')).join(', ') + '.');
    }
    const vars = {
      label_name: name(a.label),
      prob: top.length ? pct(top[0].prob) : 'n/a',
      others: top.slice(1).map((t) => name(t.label) + ' (' + pct(t.prob) + ')').join(', ') || 'none',
      confidence: a.confidence,
      unusualness,
      summary: (a.description && a.description.summary) || '',
      warnings: (a.warnings || []).join('; ') || 'none',
      reading: lines.join(' ') || 'none',
    };
    audioText = P3.replace(/\{\{(\w+)\}\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  }

  const parts = [
    'Vehicle: ' + JSON.stringify(c.vehicle || {}),
    "Driver's description: " + ((c.symptom && c.symptom.description) || ''),
    "Driver's language: " + lang,
    audioText,
    'Label ids you may use: ' + JSON.stringify(known),
  ];
  if (diagnosis.length) {
    const answers = Object.fromEntries((c.check_answers || []).map((x) => [x.check_id, x.answer]));
    const asked = diagnosis.flatMap((d) => d.checks).map((k) => `${k.id}: ${k.q} -> ${answers[k.id] || '(no answer)'}`);
    parts.push(
      'Previous causes: ' + JSON.stringify(diagnosis[diagnosis.length - 1].causes),
      'Checks asked and answers:\n' + asked.join('\n'),
      'New checks allowed: ' + (allowNew ? 'yes' : 'no')
    );
  }
  if (mode !== 'prefetch') parts.push('Use the rag_search tool to get the manual passages. Cite chunk_id values exactly as returned.');

  // search queries (port of queries() in agent2.py): symptom + sound label, the label alone, symptom + the driver's answers
  const label = labelEn[(a && a.label) || ''] || '';
  const symptom = (c.symptom && c.symptom.description) || '';
  const answered = (c.check_answers || []).map((x) => x.answer).join(' ');
  const said = (c.user_input && c.user_input.text) || ''; // the driver's own sentence first: the reranker scores full sentences far better than keywords
  const queries = [...new Set([said, `${symptom} ${label}`, label ? `${label} causes` : '', answered ? `${symptom} ${answered}` : ''].map((q) => q.trim()).filter(Boolean))];

  const v = c.vehicle || {};
  const filters = { section: ['symptoms', 'causes', 'codes', 'maintenance'] };
  for (const k of ['make', 'model', 'year']) if (v[k]) filters[k] = v[k];

  return {
    case: c,
    ctx: { n, allow_new: allowNew, asked: diagnosis.reduce((s, d) => s + d.checks.length, 0), language: lang, vehicle: { make: v.make || null, model: v.model || null },
           // Agent 1 hears a healthy engine (normal class, unusualness < 0.8): no firm fault (cap in "Ground and decide")
           healthy_audio: !!(a && a.status === 'ok' && String(a.label || '').startsWith('normal') && a.anomaly_score != null && a.anomaly_score < 0.8 && a.confidence !== 'low') },
    system_prompt: SYSTEM[n === 1 ? 1 : 2],
    user_message: parts.join('\n'),
    filters_json: JSON.stringify(filters),
    queries,
  };
}
