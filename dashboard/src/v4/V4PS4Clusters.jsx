// =====================================================================
// v2/PS4Clusters.jsx -- what the peer groups are, and how far apart.
//
// -------------------------------------------------------------------
// EVERY COORDINATE ON THIS SCREEN IS A REAL MEASUREMENT
// -------------------------------------------------------------------
// There is no synthetic projection here and no t-SNE/UMAP scatter of
// invented points. Two real sources only:
//
//   /ps4/cluster-profile  9 rows, one per fleet x cluster:
//                         scored_device_days, train_cluster_share,
//                         mean_cluster_distance, train_cluster_distance_p99,
//                         candidate_rate, actionable_rate, silhouette.
//   /ps4/weekly           per device-week: dominant_cluster_id,
//                         cluster_distance_ratio_max, anomaly_score_max.
//                         CAPPED AT 2,000 rows, all from the latest week,
//                         of 3,864 devices observed. The scatter says so.
//
// THE DISTANCE RATIO IS THE KEY AXIS. It is a device's distance from its
// own cluster centre divided by the 99th percentile distance seen for that
// cluster during training. Ratio 1.0 is therefore the edge of normal:
// below it the device sits inside the envelope its peer group occupied in
// training, above it the device is somewhere that group essentially never
// went. That line is drawn on the scatter and it is the only threshold on
// the screen that means anything absolute.
//
// THE CHARACTER LABELS ("The normal bulk", "Chronically abnormal") ARE
// MINE, NOT THE MODEL'S. They are a plain rule over actionable_rate and
// train_cluster_share, stated on screen so nobody mistakes them for
// something the clustering produced.
// =====================================================================
import React, { useMemo } from 'react';
import { CARD, CAT, INK, INK_2, INK_3, LINE, STATUS, deviceColor, deviceShort, font, nfmt, pct } from './V4theme';
import { Badge, Card, Empty, Grid, Loading, Note, Panel } from './V4Kit';
import { Bubble } from './V4Charts';

// A NULL silhouette is "not measured", not 0.000 -- Number(null) would print a perfect-looking zero.
const silText = (q) => (q.silhouette === null || q.silhouette === undefined || !Number.isFinite(Number(q.silhouette))
  ? '--' : Number(q.silhouette).toFixed(3));


// STABLE CALLBACK IDENTITIES.                                v5
// These were inline arrows in JSX, so every render produced a NEW
// function and React.memo on the chart components compared unequal
// every time -- the memo was a no-op. Every one of these closes over
// nothing but module scope, so hoisting is enough; no useCallback, no
// dependency array to get wrong. They are only invoked during render,
// so referring to a const declared further down the module is safe.
const _colorBy1 = (d) => hueFor(d.cluster);

// One hue per cluster INDEX within a fleet, assigned in fixed order so a
// cluster keeps its colour when the fleet filter changes.
const CLUSTER_HUE = [CAT[0], CAT[2], CAT[4], CAT[5], CAT[1], CAT[7]];
const hueFor = (id) => CLUSTER_HUE[Number(id) % CLUSTER_HUE.length];

// A rule, not a model output. Said out loud on screen.
function character(p) {
  const act = Number(p.actionable_rate) || 0;
  const share = Number(p.train_cluster_share) || 0;
  if (act >= 0.40) return { label: 'Chronically abnormal', tone: 'critical',
    text: 'Almost every device-day in this group gets flagged. Membership alone is a finding.' };
  if (act >= 0.15) return { label: 'Frequently flagged', tone: 'serious',
    text: 'Flagged far more often than the fleet average. Worth watching as a group.' };
  if (share >= 0.35 && act < 0.05) return { label: 'The normal bulk', tone: 'good',
    text: 'Most of the fleet lives here and it is rarely flagged. This is what normal looks like.' };
  return { label: 'Mixed', tone: 'warning',
    text: 'Neither clearly normal nor clearly troubled. Prioritise on distance, not membership.' };
}


// ---------------------------------------------------------------------
// DERIVED PROPERTIES
//
// cluster-profile gives six numbers per group and stops. Everything below
// is computed here from the per-device rows, and it is what actually gives
// each group a character rather than a number:
//
//   dominant signal   which trigger fires most often for that group.
//                     TVM group 2 is 43% hardware; TVM group 4 is 23% out
//                     of service. Same fleet, different failure mode.
//   severity mix      share of the group's device-weeks at Critical or High.
//                     Validator group 0 runs 45%; group 1 runs 18%.
//   typical score     median anomaly score. Validator group 0 sits at 0.485
//                     against group 1 at 0.308.
//   typical deviation median max |z|. How extreme the worst measurement of
//                     the week was, in standard deviations.
//   depots seen       how spread across the network the group is.
//   thin data         share of device-weeks with a low-coverage day. Worth
//                     showing: TVM group 0 is 97%, so its other numbers
//                     rest on partial weeks.
//
// CHECKED BEFORE BUILDING: "share past the edge of normal" and "median
// actionable days" came out flat across every group (4-9% and 0.0), so they
// are NOT here. A property that does not discriminate is decoration.
//
// SCOPE DIFFERS FROM THE PANEL ABOVE AND THE CARD SAYS SO. Profile figures
// come from the full scored set; these come from the 2,000-row sample of the
// latest week. Groups with fewer than 10 device-weeks in it are marked too
// thin to characterise rather than given a spurious percentage.
// ---------------------------------------------------------------------
const TRIGGER_LABEL = {
  cluster_tail: 'unlike its peers',
  event_spc: 'event rate out of control',
  oos: 'out of service',
  latency: 'slow response',
  hardware: 'hardware fault',
};

function median(v) {
  if (!v.length) return 0;
  const a = v.slice().sort((x, y) => x - y);
  const m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

function derive(rows) {
  if (!rows.length) return null;
  const n = rows.length;
  const trig = {};
  rows.forEach((r) => {
    const seen = new Set(String(r.anomaly_types || '').split('+').map((x) => x.trim()).filter((x) => x && x !== 'none'));
    seen.forEach((k) => { trig[k] = (trig[k] || 0) + 1; });
  });
  const top = Object.entries(trig).sort((a, b) => b[1] - a[1])[0];
  const raised = rows.filter((r) => r.severity === 'Critical' || r.severity === 'High').length;
  return {
    n,
    topSignal: top ? (TRIGGER_LABEL[top[0]] || top[0]) : null,
    topSignalShare: top ? top[1] / n : 0,
    raisedShare: raised / n,
    critShare: rows.filter((r) => r.severity === 'Critical').length / n,
    medScore: median(rows.map((r) => Number(r.anomaly_score_max) || 0)),
    medZ: median(rows.map((r) => Number(r.max_abs_z) || 0)),
    depots: new Set(rows.map((r) => r.facility_id)).size,
    thin: rows.filter((r) => Number(r.has_low_coverage_day) === 1).length / n,
  };
}

// ---------------------------------------------------------------------
// SEPARATION DIAGRAM
//
// One row per cluster. The bar runs from the centre (0) out to the 99th
// percentile distance that cluster reached in training -- its envelope.
// The dot sits at the cluster's mean distance now. Circle area is the
// share of training data in that cluster.
//
// Read it as: a SHORT bar is a tight group; a LONG bar is a loose one. A
// dot far along its own bar means the group is currently sitting near the
// edge of where it normally sits.
// ---------------------------------------------------------------------
function Separation({ rows, height = 44 }) {
  const max = Math.max(1, ...rows.map((r) => Number(r.train_cluster_distance_p99) || 0));
  const W = 100; // percent-based, so it scales with the card
  return (
    <div>
      {rows.map((r) => {
        const p99 = Number(r.train_cluster_distance_p99) || 0;
        const mean = Number(r.mean_cluster_distance) || 0;
        const share = Number(r.train_cluster_share) || 0;
        const act = Number(r.actionable_rate) || 0;
        const barPct = (p99 / max) * W;
        const dotPct = (mean / max) * W;
        const rad = Math.max(5, Math.min(17, Math.sqrt(share) * 26));
        const hue = hueFor(r.cluster_id);
        return (
          <div key={r.cluster_id} style={{ display: 'flex', alignItems: 'center', gap: 12, height, borderBottom: `1px solid #F5F8FB` }}>
            <div style={{ width: 104, flex: '0 0 auto' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 9, height: 9, borderRadius: 5, background: hue }} />
                <span style={{ fontSize: 12.6, fontWeight: 700, color: INK }}>Group {r.cluster_id}</span>
              </div>
              <div style={{ fontSize: 11, color: INK_3, whiteSpace: 'nowrap', ...font.num }}>{pct(share, 0)} of fleet</div>
            </div>

            <div style={{ position: 'relative', flex: 1, height: 26 }}>
              {/* the envelope: centre out to the training 99th percentile */}
              <div style={{ position: 'absolute', top: 12, left: 0, width: `${barPct}%`, height: 3, background: `${hue}44`, borderRadius: 2 }} />
              {/* the edge marker */}
              <div title={`Training 99th percentile ${p99.toFixed(2)}`}
                   style={{ position: 'absolute', top: 6, left: `calc(${barPct}% - 1px)`, width: 2, height: 15, background: hue, opacity: 0.75, borderRadius: 1 }} />
              {/* where the group sits now */}
              <div title={`Mean distance now ${mean.toFixed(2)}`}
                   style={{
                     position: 'absolute', top: 13 - rad / 2, left: `calc(${dotPct}% - ${rad / 2}px)`,
                     width: rad, height: rad, borderRadius: rad, background: hue, border: `2px solid ${CARD}`,
                   }} />
            </div>

            <div style={{ width: 96, flex: '0 0 auto', textAlign: 'right' }}>
              <div style={{ fontSize: 12.6, fontWeight: 700, color: STATUS[character(r).tone].fill, ...font.num }}>{pct(act, 1)}</div>
              <div style={{ fontSize: 11, color: INK_3 }}>flagged</div>
              <div style={{ fontSize: 11, color: INK_3, ...font.num }} title="Typical distance as a share of this group's own limit">
                {p99 ? `${((mean / p99) * 100).toFixed(0)}% of its limit` : ''}
              </div>
            </div>
          </div>
        );
      })}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11, color: INK_3 }}>
        <span>cluster centre</span>
        <span>distance --&gt;</span>
        <span>edge of normal ({max.toFixed(1)})</span>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------
// SILHOUETTE SCALE
//
// Separation has exactly ONE honest measure in what the API publishes, and
// this is it. Every fleet on one 0-1 axis with the interpretation bands
// drawn, so "0.459" stops being a bare number and becomes a position.
//
// The bands are the standard reading of a silhouette coefficient:
//   below 0.25  the groups are not really distinguishable
//   0.25 - 0.50 structure is real but the boundaries are soft
//   above 0.50  the groups can be named and acted on
// These are the same cuts the API's own separation_verdict uses, which is
// why the badge and the position on this scale always agree.
// ---------------------------------------------------------------------
function SilhouetteScale({ rows }) {
  if (!rows.length) return null;
  const BANDS = [
    { to: 0.25, fill: '#FEF2F2', label: 'not separable' },
    { to: 0.50, fill: '#FFFBEB', label: 'weak but real' },
    { to: 1.00, fill: '#ECFDF5', label: 'well separated' },
  ];
  let prev = 0;
  return (
    <div>
      <div style={{ position: 'relative', height: 46, borderRadius: 10, overflow: 'hidden', border: `1px solid ${LINE}` }}>
        {BANDS.map((b) => {
          const left = prev * 100; const width = (b.to - prev) * 100; prev = b.to;
          return (
            <div key={b.to} style={{ position: 'absolute', left: `${left}%`, width: `${width}%`, top: 0, bottom: 0, background: b.fill, borderRight: `1px solid ${LINE}` }}>
              <span style={{ position: 'absolute', left: 8, top: 6, fontSize: 11, color: INK_3, fontWeight: 600, letterSpacing: '.04em', textTransform: 'uppercase' }}>
                {b.label}
              </span>
            </div>
          );
        })}
        {rows.map((q) => {
          const v = Math.max(0, Math.min(1, Number(q.silhouette) || 0));
          return (
            <div key={q.device_type}
                 title={`${deviceShort(q.device_type)} ${v.toFixed(3)} - ${q.separation_verdict || ''}`}
                 style={{ position: 'absolute', left: `calc(${v * 100}% - 1px)`, top: 20, bottom: 4, width: 2, background: deviceColor(q.device_type), borderRadius: 1 }} />
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: INK_3, marginTop: 4, ...font.num }}>
        <span>0.0</span><span>0.25</span><span>0.50</span><span>1.0</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginTop: 10 }}>
        {rows.map((q) => (
          <span key={q.device_type} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.2 }}>
            <span style={{ width: 9, height: 9, borderRadius: 5, background: deviceColor(q.device_type) }} />
            <strong style={{ color: INK }}>{deviceShort(q.device_type)}</strong>
            <span style={{ ...font.num, color: INK_2 }}>{silText(q)}</span>
            {q.quality_source && q.quality_source !== 'manifest' && (
              <Badge tone="warning" title={`quality_source=${q.quality_source}`}>
                {q.quality_source === 'not_published' ? 'not published for this run' : 'from the run log, not this run\'s manifest'}
              </Badge>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function PS4Clusters({ quality, profile, weeklyFeed, typeOn, onDevice }) {
  // weeklyFeed is the whole {rows, loading, idle, error} object on purpose.
  // Passing only rows meant an in-flight fetch rendered as "No scored
  // device-weeks for this fleet" -- a definitive-sounding empty state
  // covering for a request that had not come back yet. The separation
  // diagram loads instantly (9 rows); the scatter needs 2,000, so the two
  // halves of this card genuinely do arrive at different times.
  const weekly = (weeklyFeed && weeklyFeed.rows) || [];
  const weeklyPending = !weeklyFeed || weeklyFeed.loading || weeklyFeed.idle;
  const weeklyFailed = !!(weeklyFeed && weeklyFeed.error);
  const fleets = useMemo(
    () => ['GATE', 'TVM', 'VALIDATOR'].filter((t) => typeOn(t) && profile.some((p) => String(p.device_type).toUpperCase() === t)),
    [profile, typeOn]
  );

  // Per-device points, straight from /ps4/weekly. No projection, no jitter.
  const points = useMemo(() => (weekly || [])
    .filter((r) => typeOn(r.device_type) && r.dominant_cluster_id !== null && r.dominant_cluster_id !== undefined)
    .map((r) => ({
      device_id: r.device_id,
      device_type: r.device_type,
      cluster: Number(r.dominant_cluster_id),
      ratio: Number(r.cluster_distance_ratio_max) || 0,
      score: Number(r.anomaly_score_max) || 0,
      days: Math.max(1, Number(r.actionable_days) || 0),
      severity: r.severity,
      raw: r,
    })), [weekly, typeOn]);

  const derived = useMemo(() => {
    const by = {};
    points.forEach((p) => {
      const k = `${String(p.device_type).toUpperCase()}|${p.cluster}`;
      (by[k] = by[k] || []).push(p.raw);
    });
    const out = {};
    Object.entries(by).forEach(([k, rows]) => { out[k] = derive(rows); });
    return out;
  }, [points]);

  if (!profile.length) return <Empty height={200}>Cluster profile not loaded.</Empty>;

  const scaleRows = quality.filter((q) => typeOn(q.device_type));

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <Panel title="How separable the groups are"
             hint="One measure, all three fleets. Where a fleet's marker falls decides whether you can act on which group a device is in, or only on how far it sits from its own.">
        <SilhouetteScale rows={scaleRows} />
        <Note>
          This is the only true separation measure the pipeline publishes. Bus validators land in the middle band,
          which is why their guidance differs from the other two: use distance, not membership.
        </Note>
      </Panel>

      {fleets.map((t) => {
        const rows = profile
          .filter((p) => String(p.device_type).toUpperCase() === t)
          .slice()
          .sort((a, b) => Number(b.train_cluster_share) - Number(a.train_cluster_share));
        const q = quality.find((x) => String(x.device_type).toUpperCase() === t) || {};
        const pts = points.filter((p) => String(p.device_type).toUpperCase() === t);

        return (
          <Card key={t} style={{ borderLeft: `3px solid ${deviceColor(t)}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 4 }}>
              <h3 style={{ ...font.h3, margin: 0 }}>{deviceShort(t)}</h3>
              <span style={{ ...font.num, fontSize: 13.1, color: INK_2 }}>separation {silText(q)}</span>
              {q.separation_verdict && (
                <Badge tone={Number(q.silhouette) >= 0.5 ? 'good' : 'warning'}>{q.separation_verdict}</Badge>
              )}
              <span style={{ fontSize: 12.2, color: INK_3 }}>{rows.length} groups</span>
            </div>
            {q.separation_note && <p style={{ ...font.note, margin: '0 0 14px' }}>{q.separation_note}</p>}

            <Grid cols="minmax(300px,1fr) minmax(320px,1.1fr)" gap={18}>
              <div>
                <div style={{ ...font.micro, marginBottom: 8 }}>How tight each group is</div>
                <Separation rows={rows} />
              </div>

              <div>
                <div style={{ ...font.micro, marginBottom: 8 }}>Where each device sat this week</div>
                {weeklyPending ? (
                  <Loading height={260} label="Loading device positions" />
                ) : weeklyFailed ? (
                  <Empty height={260}>Device positions could not be loaded. The group profile above is unaffected.</Empty>
                ) : pts.length ? (
                  <Bubble
                    data={pts}
                    xKey="ratio" yKey="score" zKey="days" nameKey="device_id"
                    xLabel="Distance from its group, against that group's normal limit"
                    yLabel="Anomaly score"
                    height={260}
                    colorBy={_colorBy1}
                    onDrill={(d) => d && d.device_id && onDevice && onDevice(d.device_id)}
                  />
                ) : (
                  <Empty height={260}>
                    No device from this fleet appears in the 2,000-row sample the API returns.
                  </Empty>
                )}
                <p style={{ ...font.note, fontSize: 11.2, margin: '6px 0 0' }}>
                  Past 1.0 on the horizontal axis, a device is further from its group than that group ever normally
                  went. Colour is the group; bubble size is days flagged that week.
                </p>
              </div>
            </Grid>

            <div style={{ ...font.micro, margin: '18px 0 8px' }}>What each group is</div>
            <Grid cols="repeat(auto-fit,minmax(250px,1fr))" gap={10}>
              {rows.map((r) => {
                const c = character(r);
                return (
                  <div key={r.cluster_id} style={{ border: `1px solid ${LINE}`, borderRadius: 11, padding: '12px 13px', borderTop: `3px solid ${hueFor(r.cluster_id)}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                      <strong style={{ fontSize: 13.1, color: INK }}>Group {r.cluster_id}</strong>
                      <Badge tone={c.tone}>{c.label}</Badge>
                    </div>
                    <p style={{ ...font.note, fontSize: 12.2, margin: '0 0 10px' }}>{c.text}</p>
                    <Prop label="Share of the fleet" value={pct(r.train_cluster_share, 1)} />
                    <Prop label="Device-days scored" value={nfmt(r.scored_device_days)} />
                    <Prop label="Flagged rate" value={pct(r.actionable_rate, 1)}
                          tone={Number(r.actionable_rate) >= 0.4 ? 'critical' : Number(r.actionable_rate) >= 0.15 ? 'serious' : null} />
                    <Prop label="Looked at rate" value={pct(r.candidate_rate, 1)} />
                    <Prop label="Typical distance" value={Number(r.mean_cluster_distance).toFixed(2)} />
                    <Prop label="Edge of normal" value={Number(r.train_cluster_distance_p99).toFixed(2)} />

                    {(() => {
                      const dv = derived[`${t}|${Number(r.cluster_id)}`];
                      if (!dv) return null;
                      return (
                        <div style={{ marginTop: 10, paddingTop: 9, borderTop: `1px dashed ${LINE}` }}>
                          <div style={{ ...font.micro, marginBottom: 6 }}>This week ({nfmt(dv.n)} device-weeks)</div>
                          {dv.n < 10 ? (
                            <p style={{ ...font.note, fontSize: 11.2, margin: 0 }}>
                              Too few in this week's sample to characterise.
                            </p>
                          ) : (
                            <>
                              {dv.topSignal && (
                                <Prop label="Most common signal" value={`${dv.topSignal} ${pct(dv.topSignalShare, 0)}`} />
                              )}
                              <Prop label="Critical or High" value={pct(dv.raisedShare, 0)}
                                    tone={dv.raisedShare >= 0.4 ? 'critical' : dv.raisedShare >= 0.2 ? 'serious' : null} />
                              <Prop label="Typical anomaly score" value={dv.medScore.toFixed(3)} />
                              <Prop label="Typical deviation" value={`${dv.medZ.toFixed(1)} sigma`} />
                              <Prop label="Depots seen in" value={nfmt(dv.depots)} />
                              <Prop label="Weeks with thin data" value={pct(dv.thin, 0)}
                                    tone={dv.thin >= 0.6 ? 'warning' : null} />
                            </>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                );
              })}
            </Grid>
          </Card>
        );
      })}

      <Note>
        There is no two-dimensional map of the clusters here because the pipeline does not publish one. What it
        gives per device is the distance to its OWN group's centre, normalised by that group's limit - not the
        cluster centroids and not the underlying feature vectors. Two devices in different groups showing 0.9 are
        each nine-tenths of the way to their own edge; they are not the same distance from anything shared. A real
        cluster map would need the notebook to export two projected coordinates per device-week.
        {' '}The group names above are a plain rule applied here - chronically abnormal at or above 40% flagged, frequently
        flagged at or above 15%, the normal bulk when a group holds over a third of the fleet and is flagged under 5%.
        The clustering itself produces numbered groups and no names. The scatter uses the 2,000 device-weeks the API
        returns for the latest week, of 3,864 devices observed.
      </Note>
    </div>
  );
}

function Prop({ label, value, tone }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '3px 0' }}>
      <span style={{ fontSize: 11.2, color: INK_3 }}>{label}</span>
      <span style={{ fontSize: 12.2, fontWeight: 700, color: tone ? STATUS[tone].fill : INK, ...font.num }}>{value}</span>
    </div>
  );
}

// FONTS_SCALED 04-Aug-2026
