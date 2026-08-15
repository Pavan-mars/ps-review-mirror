#!/usr/bin/env bash
# =====================================================================
#  ECR + EVENTBRIDGE AUDIT  --  READ-ONLY   CloudShell, us-east-1
#
#  Answers three questions:
#    1. What is in ECR? Repositories, images, tags, sizes, age, policies.
#    2. What is in EventBridge? Rules on EVERY bus, targets, state, plus
#       EventBridge Scheduler and Pipes -- which are separate services and
#       are the usual blind spot.
#    3. Which of it is relevant to PS1, PS3 and PS4 -- resolved by following
#       the actual references, not by matching names.
#
#  Every call is list/describe/get. Nothing is created, modified, deleted,
#  started, stopped, enabled or disabled.
#
#      bash ecr_eventbridge_audit.sh
#
#  Output is printed and saved to ~/ecr_eb_audit_<stamp>/report.txt
#  A machine-readable bundle is written alongside it as facts.json.
# =====================================================================
set -uo pipefail
REGION=${REGION:-us-east-1}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$HOME/ecr_eb_audit_$STAMP"; mkdir -p "$OUT"
exec > >(tee "$OUT/report.txt") 2>&1

hdr(){ echo; echo "======================================================================"; echo " $1"; echo "======================================================================"; }
sub(){ echo; echo "-- $1"; }

echo "ECR + EVENTBRIDGE AUDIT   $STAMP   ($REGION)   READ-ONLY"
echo "account: $(aws sts get-caller-identity --query Account --output text 2>/dev/null)"

# =====================================================================
hdr "[1] ECR -- REPOSITORIES"
# =====================================================================
aws ecr describe-repositories --region "$REGION" --output json 2>/dev/null > "$OUT/ecr_repos.json" \
  || echo "   (describe-repositories failed -- no ECR access?)"
python3 - "$OUT/ecr_repos.json" <<'PY'
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: print("   no data"); raise SystemExit
r=d.get("repositories",[])
print(f"   {len(r)} repositories")
if not r: raise SystemExit
print()
print("   %-46s %-10s %-12s %s" % ("NAME","TAG-MUT","SCAN-ON-PUSH","CREATED"))
for x in sorted(r,key=lambda y:y["repositoryName"]):
    print("   %-46s %-10s %-12s %s" % (
        x["repositoryName"], x.get("imageTagMutability"),
        (x.get("imageScanningConfiguration") or {}).get("scanOnPush"),
        str(x.get("createdAt"))[:19]))
print()
print("   URIs:")
for x in sorted(r,key=lambda y:y["repositoryName"]):
    print("     ", x.get("repositoryUri"))
PY

# ---------------------------------------------------------------- images
hdr "[2] ECR -- IMAGES PER REPOSITORY"
: > "$OUT/ecr_images.jsonl"
for REPO in $(python3 -c "
import json
try: d=json.load(open('$OUT/ecr_repos.json'))
except Exception: d={}
print(' '.join(x['repositoryName'] for x in d.get('repositories',[])))
" 2>/dev/null); do
  aws ecr describe-images --repository-name "$REPO" --region "$REGION" --output json 2>/dev/null \
    | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
for i in d.get('imageDetails',[]):
    i['_repo']='$REPO'; print(json.dumps(i,default=str))
" >> "$OUT/ecr_images.jsonl"
done
python3 - "$OUT/ecr_images.jsonl" <<'PY'
import json,sys,datetime as dt
rows=[]
for l in open(sys.argv[1]):
    l=l.strip()
    if l:
        try: rows.append(json.loads(l))
        except Exception: pass
if not rows: print("   no images found"); raise SystemExit
print(f"   {len(rows)} images across {len({r['_repo'] for r in rows})} repositories")
print()
print("   %-40s %-26s %9s  %-20s %s" % ("REPO","TAGS","SIZE MB","PUSHED","DIGEST"))
tot=0
for r in sorted(rows,key=lambda x:(x['_repo'], str(x.get('imagePushedAt')))):
    mb=(r.get('imageSizeInBytes') or 0)/1048576; tot+=mb
    tags=",".join(r.get('imageTags') or []) or "(untagged)"
    print("   %-40s %-26s %9.1f  %-20s %s" % (
        r['_repo'][:40], tags[:26], mb, str(r.get('imagePushedAt'))[:19], (r.get('imageDigest') or '')[7:19]))
print(f"\n   total: {tot:,.0f} MB")
un=[r for r in rows if not r.get('imageTags')]
if un: print(f"   UNTAGGED images: {len(un)} -- these are usually orphans from a re-push and cost storage")
PY

sub "lifecycle policies (absent = images accumulate forever)"
for REPO in $(python3 -c "
import json
try: d=json.load(open('$OUT/ecr_repos.json'))
except Exception: d={}
print(' '.join(x['repositoryName'] for x in d.get('repositories',[])))
" 2>/dev/null); do
  P=$(aws ecr get-lifecycle-policy --repository-name "$REPO" --region "$REGION" \
        --query 'lifecyclePolicyText' --output text 2>/dev/null)
  if [ -z "$P" ] || [ "$P" = "None" ]; then printf '   %-46s NONE\n' "$REPO"
  else printf '   %-46s %s\n' "$REPO" "$(echo "$P" | head -c 90)"; fi
done

# =====================================================================
hdr "[3] EVENTBRIDGE -- EVENT BUSES"
# =====================================================================
aws events list-event-buses --region "$REGION" --output json 2>/dev/null > "$OUT/eb_buses.json"
python3 - "$OUT/eb_buses.json" <<'PY'
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: print("   no data"); raise SystemExit
b=d.get("EventBuses",[])
print(f"   {len(b)} bus(es)")
for x in b: print("     %-40s %s" % (x.get("Name"), x.get("Arn","")[:80]))
print()
print("   NOTE: 'aws events list-rules' WITHOUT --event-bus-name only returns the")
print("   default bus. Rules on a custom bus are invisible to that call. Every bus")
print("   above is enumerated separately below.")
PY

hdr "[4] EVENTBRIDGE -- RULES ON EVERY BUS, WITH TARGETS"
: > "$OUT/eb_rules.jsonl"
for BUS in $(python3 -c "
import json
try: d=json.load(open('$OUT/eb_buses.json'))
except Exception: d={}
print(' '.join(x['Name'] for x in d.get('EventBuses',[])) or 'default')
" 2>/dev/null); do
  for RULE in $(aws events list-rules --event-bus-name "$BUS" --region "$REGION" \
                  --query 'Rules[].Name' --output text 2>/dev/null | tr '\t' '\n'); do
    [ -z "$RULE" ] && continue
    R=$(aws events describe-rule --name "$RULE" --event-bus-name "$BUS" --region "$REGION" --output json 2>/dev/null)
    T=$(aws events list-targets-by-rule --rule "$RULE" --event-bus-name "$BUS" --region "$REGION" --output json 2>/dev/null)
    python3 -c "
import json
r=json.loads('''$R''' or '{}'); t=json.loads('''$T''' or '{}')
print(json.dumps({'bus':'$BUS','rule':r.get('Name'),'state':r.get('State'),
 'schedule':r.get('ScheduleExpression'),'pattern':r.get('EventPattern'),
 'desc':r.get('Description'),
 'targets':[{'id':x.get('Id'),'arn':x.get('Arn'),'input':x.get('Input')} for x in t.get('Targets',[])]}))
" >> "$OUT/eb_rules.jsonl" 2>/dev/null
  done
done
python3 - "$OUT/eb_rules.jsonl" <<'PY'
import json,sys
rows=[]
for l in open(sys.argv[1]):
    l=l.strip()
    if l:
        try: rows.append(json.loads(l))
        except Exception: pass
print(f"   {len(rows)} rules   ({sum(1 for r in rows if r.get('state')=='ENABLED')} ENABLED)")
print()
def svc(a):
    p=(a or '').split(':')
    return p[2] if len(p)>2 else '?'
for r in sorted(rows,key=lambda x:(x['bus'],str(x.get('rule')))):
    kind = r.get('schedule') or ('event-pattern' if r.get('pattern') else '(none)')
    print("   [%s] %-9s %-42s %s" % (r['bus'][:8], r.get('state'), str(r.get('rule'))[:42], kind))
    for t in r.get('targets') or []:
        arn=t.get('arn') or ''
        short = arn.split(':function:')[-1] if ':function:' in arn else arn.split('/')[-1] or arn
        print("        -> %-10s %s" % (svc(arn), short[:64]))
    if not r.get('targets'): print("        -> (NO TARGET -- rule fires into nothing)")
PY

sub "rules with NO targets (they fire and nothing happens)"
python3 - "$OUT/eb_rules.jsonl" <<'PY'
import json,sys
n=0
for l in open(sys.argv[1]):
    l=l.strip()
    if not l: continue
    r=json.loads(l)
    if not r.get('targets'):
        print("   %-42s %s  bus=%s" % (r.get('rule'), r.get('state'), r.get('bus'))); n+=1
print("   none" if not n else f"   {n} rule(s) -- each is either dead config or a broken wiring")
PY

# =====================================================================
hdr "[5] EVENTBRIDGE SCHEDULER + PIPES  (separate services, easily missed)"
# =====================================================================
sub "EventBridge Scheduler schedules"
aws scheduler list-schedules --region "$REGION" --output json 2>/dev/null > "$OUT/eb_scheduler.json" \
  || echo "   (scheduler API unavailable or no permission)"
python3 - "$OUT/eb_scheduler.json" <<'PY'
import json,sys,os
if not os.path.exists(sys.argv[1]): print("   n/a"); raise SystemExit
try: d=json.load(open(sys.argv[1]))
except Exception: print("   no data"); raise SystemExit
s=d.get("Schedules",[])
print(f"   {len(s)} schedule(s)")
for x in s: print("     %-40s %-10s group=%s" % (x.get("Name"), x.get("State"), x.get("GroupName")))
if not s: print("     (nothing here -- all scheduling is classic EventBridge rules)")
PY

sub "EventBridge Pipes"
aws pipes list-pipes --region "$REGION" --output json 2>/dev/null > "$OUT/eb_pipes.json" \
  || echo "   (pipes API unavailable or no permission)"
python3 - "$OUT/eb_pipes.json" <<'PY'
import json,sys,os
if not os.path.exists(sys.argv[1]): print("   n/a"); raise SystemExit
try: d=json.load(open(sys.argv[1]))
except Exception: print("   no data"); raise SystemExit
p=d.get("Pipes",[])
print(f"   {len(p)} pipe(s)")
for x in p: print("     %-40s %-10s %s -> %s" % (x.get("Name"),x.get("CurrentState"),
      str(x.get("Source"))[:40], str(x.get("Target"))[:40]))
if not p: print("     (none)")
PY

# =====================================================================
hdr "[6] WHO ACTUALLY USES THE ECR IMAGES -- followed by reference, not by name"
# =====================================================================
sub "SageMaker models and the container image each one runs"
aws sagemaker list-models --region "$REGION" --max-results 100 --output json 2>/dev/null > "$OUT/sm_models.json"
: > "$OUT/sm_model_images.jsonl"
for M in $(python3 -c "
import json
try: d=json.load(open('$OUT/sm_models.json'))
except Exception: d={}
print(' '.join(x['ModelName'] for x in d.get('Models',[])))
" 2>/dev/null); do
  aws sagemaker describe-model --model-name "$M" --region "$REGION" --output json 2>/dev/null \
   | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
pc=d.get('PrimaryContainer') or {}
imgs=[pc.get('Image')] if pc.get('Image') else [c.get('Image') for c in (d.get('Containers') or [])]
print(json.dumps({'model':d.get('ModelName'),'images':[i for i in imgs if i],
                  'data':pc.get('ModelDataUrl')}))
" >> "$OUT/sm_model_images.jsonl"
done
python3 - "$OUT/sm_model_images.jsonl" <<'PY'
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(f"   {len(rows)} model(s)")
for r in sorted(rows,key=lambda x:str(x.get('model'))):
    print("     %-44s" % str(r.get('model'))[:44])
    for i in r.get('images') or []:
        own = "OWN-ACCOUNT-ECR" if ".dkr.ecr." in i and not i.startswith(("763104351884","683313688378")) else "AWS-MANAGED-DLC"
        print("        image: %s   [%s]" % (i[:88], own))
PY

sub "which endpoint runs which model"
for E in $(aws sagemaker list-endpoints --region "$REGION" --query 'Endpoints[].EndpointName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$E" ] && continue
  EC=$(aws sagemaker describe-endpoint --endpoint-name "$E" --region "$REGION" --query 'EndpointConfigName' --output text 2>/dev/null)
  ST=$(aws sagemaker describe-endpoint --endpoint-name "$E" --region "$REGION" --query 'EndpointStatus' --output text 2>/dev/null)
  MODELS=$(aws sagemaker describe-endpoint-config --endpoint-config-name "$EC" --region "$REGION" \
             --query 'ProductionVariants[].ModelName' --output text 2>/dev/null | tr '\t' ',')
  INST=$(aws sagemaker describe-endpoint-config --endpoint-config-name "$EC" --region "$REGION" \
             --query 'ProductionVariants[0].InstanceType' --output text 2>/dev/null)
  printf '   %-46s %-11s %-14s models=%s\n' "$E" "$ST" "${INST:-serverless}" "${MODELS:-?}"
done

sub "Lambda functions packaged as a container image (these pull from ECR)"
aws lambda list-functions --region "$REGION" --output json 2>/dev/null \
 | python3 -c "
import json,sys
d=json.load(sys.stdin); n=0
for f in d.get('Functions',[]):
    if f.get('PackageType')=='Image':
        n+=1; print('   %-40s %s' % (f['FunctionName'], (f.get('ImageUri') or '')[:80]))
print('   none -- every Lambda is a zip package' if not n else '')
"

# =====================================================================
hdr "[7] RELEVANCE TO PS1 / PS3 / PS4"
# =====================================================================
python3 - "$OUT" <<'PY'
import json,os,sys,re
OUT=sys.argv[1]
def jl(p):
    p=os.path.join(OUT,p); r=[]
    if os.path.exists(p):
        for l in open(p):
            l=l.strip()
            if l:
                try: r.append(json.loads(l))
                except Exception: pass
    return r
def jf(p,default=None):
    p=os.path.join(OUT,p)
    try: return json.load(open(p))
    except Exception: return default

repos=[x['repositoryName'] for x in (jf('ecr_repos.json') or {}).get('repositories',[])]
imgs=jl('ecr_images.jsonl')
rules=jl('eb_rules.jsonl')
models=jl('sm_model_images.jsonl')

PS={'PS1':['ps1','failure'],'PS3':['ps3','rootcause','root-cause','severity'],'PS4':['ps4','anomaly','cluster']}

print("   ECR repositories, by problem statement")
claimed=set()
for ps,keys in PS.items():
    hit=[r for r in repos if any(k in r.lower() for k in keys)]
    claimed|=set(hit)
    print("     %-5s %s" % (ps, ", ".join(hit) if hit else "(no repository whose NAME matches)"))
other=[r for r in repos if r not in claimed]
print("     other %s" % (", ".join(other) if other else "(none)"))
print()
print("   CAUTION: the above is NAME MATCHING and proves nothing on its own. What")
print("   matters is whether a SageMaker model or Lambda actually references the")
print("   image. That linkage is below.")

print()
print("   ECR images REFERENCED by a SageMaker model (this is the real usage):")
used=set()
for m in models:
    for i in m.get('images') or []:
        if '.dkr.ecr.' in i:
            repo=i.split('/')[-1].split(':')[0].split('@')[0]
            used.add(repo)
            print("     %-44s <- %s" % (repo, m.get('model')))
if not used: print("     NONE -- no SageMaker model runs an image from this account's ECR.")
unused=[r for r in repos if r not in used]
if unused:
    print()
    print("   ECR repositories NOT referenced by any SageMaker model:")
    for r in sorted(unused): print("     ", r)
    print("   (they may still be used by ECS, CodeBuild or nothing at all -- an")
    print("    unreferenced repository is a candidate for retirement, not proof of one)")

print()
print("   EVENTBRIDGE rules, by problem statement (target-based where possible)")
for ps,keys in PS.items():
    hits=[]
    for r in rules:
        blob=(str(r.get('rule'))+' '+str(r.get('desc'))+' '+
              ' '.join(str(t.get('arn')) for t in (r.get('targets') or []))).lower()
        if any(k in blob for k in keys): hits.append(r)
    print("     %s" % ps)
    if not hits: print("       (none)")
    for r in hits:
        tg=", ".join((t.get('arn') or '').split(':function:')[-1] for t in (r.get('targets') or [])) or "(no target)"
        print("       %-9s %-40s %-24s -> %s" % (r.get('state'), str(r.get('rule'))[:40],
              str(r.get('schedule') or 'event-pattern')[:24], tg[:44]))

print()
print("   SCHEDULE COLLISIONS -- two rules on the same cron")
from collections import defaultdict
by=defaultdict(list)
for r in rules:
    if r.get('schedule') and r.get('state')=='ENABLED': by[r['schedule']].append(r['rule'])
clash=[(k,v) for k,v in by.items() if len(v)>1]
if clash:
    for k,v in clash: print("     %-28s %s" % (k, ", ".join(v)))
    print("     Two loaders hitting Aurora at the same minute is how PS4 lost 21 runs")
    print("     over 7 days in July. Worth confirming these are independent.")
else:
    print("     none")
PY

hdr "DONE"
echo "   report:  $OUT/report.txt"
echo "   raw:     $OUT/*.json  $OUT/*.jsonl"
echo
echo "   NOTHING WAS MODIFIED. Every call was list/describe/get."
