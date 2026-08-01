import sys, re
F = sys.argv[1]
s = open(F, encoding="utf-8").read()
orig = s
if "liveFeatImpVAL" in s:
    print("already patched"); sys.exit(0)
n = 0
def rep(old, new, count=1):
    global s, n
    assert s.count(old) >= 1, f"NOT FOUND: {old[:70]!r}"
    s = s.replace(old, new, count); n += 1

# 1 - selector list
rep("""// PS1 only has TVM and GATE models — exclude Validators from all PS1 selectors
const PS1_DEVICES = ['TVMs', 'Gates'];""",
"""// PS1 covers TVM, GATE and VALIDATOR. Validators entered PS1 with the 2026-07-24
// hardware-OOS wave: they have no chargeable events, so will_hardware_oos_3d is the
// only label available for them - which is also why PS1's target is OOS, not chargeable.
const PS1_DEVICES = ['TVMs', 'Gates', 'Validators'];
const PS1_CAT_COLOR = { TVM: '#6366f1', GATE: '#10b981', VALIDATOR: '#f59e0b' };""")

# 2 - state
rep("  const [liveFeatImpGATE, setLiveFeatImpGATE] = useState([]);",
    "  const [liveFeatImpGATE, setLiveFeatImpGATE] = useState([]);\n"
    "  const [liveFeatImpVAL, setLiveFeatImpVAL] = useState([]);")

# 3 - fetch
rep("""      fetch(`${API_BASE}/ps1/feature-importance?device_category=GATE`).then((r) => r.json()),
    ]).then(([tvm, gate]) => {
      setLiveFeatImpTVM(Array.isArray(tvm) ? tvm : []);
      setLiveFeatImpGATE(Array.isArray(gate) ? gate : []);""",
"""      fetch(`${API_BASE}/ps1/feature-importance?device_category=GATE`).then((r) => r.json()),
      fetch(`${API_BASE}/ps1/feature-importance?device_category=VALIDATOR`).then((r) => r.json()),
    ]).then(([tvm, gate, val]) => {
      setLiveFeatImpTVM(Array.isArray(tvm) ? tvm : []);
      setLiveFeatImpGATE(Array.isArray(gate) ? gate : []);
      setLiveFeatImpVAL(Array.isArray(val) ? val : []);""")

# 4 - feature comparison
rep("""  // Feature comparison: merge TVM + GATE importance into top-15 union, sorted by max(TVM, GATE)""",
    """  // Feature comparison: merge TVM + GATE + VALIDATOR importance into a top-15 union""")
rep("""    liveFeatImpGATE.forEach((f) => {
      if (!all[f.feature_name]) all[f.feature_name] = { feature: f.feature_name };
      all[f.feature_name].GATE = parseFloat(f.avg_importance);
    });
    return Object.values(all)
      .sort((a, b) => Math.max(b.TVM || 0, b.GATE || 0) - Math.max(a.TVM || 0, a.GATE || 0))
      .slice(0, 15);
  }, [liveFeatImpTVM, liveFeatImpGATE]);""",
"""    liveFeatImpGATE.forEach((f) => {
      if (!all[f.feature_name]) all[f.feature_name] = { feature: f.feature_name };
      all[f.feature_name].GATE = parseFloat(f.avg_importance);
    });
    liveFeatImpVAL.forEach((f) => {
      if (!all[f.feature_name]) all[f.feature_name] = { feature: f.feature_name };
      all[f.feature_name].VALIDATOR = parseFloat(f.avg_importance);
    });
    return Object.values(all)
      .sort((a, b) => Math.max(b.TVM || 0, b.GATE || 0, b.VALIDATOR || 0)
                    - Math.max(a.TVM || 0, a.GATE || 0, a.VALIDATOR || 0))
      .slice(0, 15);
  }, [liveFeatImpTVM, liveFeatImpGATE, liveFeatImpVAL]);""")

# 5 - causation selector
rep("""    const src = causalDevice === 'Gates' ? liveFeatImpGATE : liveFeatImpTVM;""",
"""    const src = causalDevice === 'Gates' ? liveFeatImpGATE
              : causalDevice === 'Validators' ? liveFeatImpVAL
              : liveFeatImpTVM;""")
rep("""  }, [causalDevice, liveFeatImpTVM, liveFeatImpGATE]);""",
"""  }, [causalDevice, liveFeatImpTVM, liveFeatImpGATE, liveFeatImpVAL]);""")

# 6 - risk-trend chart series
rep("""                  <Bar yAxisId="count" dataKey="GATE_fail" name="GATE Failures Flagged" fill="#10b981" opacity={0.4} radius={[2, 2, 0, 0]} />""",
"""                  <Bar yAxisId="count" dataKey="GATE_fail" name="GATE Failures Flagged" fill="#10b981" opacity={0.4} radius={[2, 2, 0, 0]} />
                  <Bar yAxisId="count" dataKey="VALIDATOR_fail" name="VALIDATOR Failures Flagged" fill="#f59e0b" opacity={0.4} radius={[2, 2, 0, 0]} />""")
rep("""                  <Line yAxisId="prob" type="monotone" dataKey="GATE_prob" name="GATE Avg Risk %" stroke="#10b981" strokeWidth={2} dot={false} />""",
"""                  <Line yAxisId="prob" type="monotone" dataKey="GATE_prob" name="GATE Avg Risk %" stroke="#10b981" strokeWidth={2} dot={false} />
                  <Line yAxisId="prob" type="monotone" dataKey="VALIDATOR_prob" name="VALIDATOR Avg Risk %" stroke="#f59e0b" strokeWidth={2} dot={false} />""")

# 7 - hardware breakdown bars
rep("""                    <Bar dataKey="GATE_fail" name="GATE Failures" fill="#10b981" opacity={0.8} radius={[2, 2, 0, 0]} />""",
"""                    <Bar dataKey="GATE_fail" name="GATE Failures" fill="#10b981" opacity={0.8} radius={[2, 2, 0, 0]} />
                    <Bar dataKey="VALIDATOR_fail" name="VALIDATOR Failures" fill="#f59e0b" opacity={0.8} radius={[2, 2, 0, 0]} />""")

# 8 - feature-importance comparison chart
rep("""                <div className="card-header" style={{ marginBottom: 0 }}>Feature Importance: TVM vs GATE (Top 15)</div>""",
"""                <div className="card-header" style={{ marginBottom: 0 }}>Feature Importance: TVM vs GATE vs VALIDATOR (Top 15)</div>""")
rep("""                  <Bar dataKey="GATE" name="GATE Avg |SHAP|" fill="#10b981" radius={[0, 4, 4, 0]} />""",
"""                  <Bar dataKey="GATE" name="GATE Avg |SHAP|" fill="#10b981" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="VALIDATOR" name="VALIDATOR Avg |SHAP|" fill="#f59e0b" radius={[0, 4, 4, 0]} />""")

# 9 - category colours: make every site validator-aware
s = s.replace("""const c = m.device_category === 'TVM' ? '#6366f1' : '#10b981';""",
              """const c = PS1_CAT_COLOR[m.device_category] || '#10b981';"""); n += 1
s = s.replace("""const catColor = m.device_category === 'TVM' ? '#6366f1' : '#10b981';""",
              """const catColor = PS1_CAT_COLOR[m.device_category] || '#10b981';"""); n += 1
s = s.replace("""const catColor = m.category === 'TVM' ? '#6366f1' : '#10b981';""",
              """const catColor = PS1_CAT_COLOR[m.category] || '#10b981';"""); n += 1
s = s.replace("""const catColor = m.device_category === 'TVM' ? '#6366f1' : m.device_category === 'GATE' ? '#10b981' : '#f59e0b';""",
              """const catColor = PS1_CAT_COLOR[m.device_category] || '#f59e0b';"""); n += 1

# 10 - KPI labels
s = s.replace(">TVM + GATE<", ">TVM + GATE + VALIDATOR<")
s = s.replace("TVM + GATE champion models", "TVM + GATE + VALIDATOR champion models")

open(F, "w", encoding="utf-8").write(s)
print(f"{n} targeted edits applied")
for tok in ["'Validators'", "liveFeatImpVAL", "device_category=VALIDATOR", "VALIDATOR_fail",
            "VALIDATOR_prob", "PS1_CAT_COLOR", 'dataKey="VALIDATOR"']:
    print(f"   {tok:28} x{s.count(tok)}")
print("brace delta:", s.count("{") - s.count("}"), " paren delta:", s.count("(") - s.count(")"))
