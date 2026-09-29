// Build P1 Poseidon trees exclusively from a sealed admission-registry snapshot.
// Output paths are public enrollment material. No holder secret is read or emitted.
import {readFileSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {buildPoseidon} from 'circomlibjs';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw Error('expected sealed snapshot and output paths');
const publication = JSON.parse(readFileSync(input, 'utf8'));
const canonical = x => {
  if (Array.isArray(x)) return `[${x.map(canonical).join(',')}]`;
  if (x !== null && typeof x === 'object')
    return `{${Object.keys(x).sort().map(k => `${JSON.stringify(k)}:${canonical(x[k])}`).join(',')}}`;
  return JSON.stringify(x);
};
const sha = x => createHash('sha256').update(canonical(x)).digest('hex');
// The runner exports all numeric fields as decimal strings, hashes that transport
// snapshot, and retains the original SQLite snapshot separately as sealed.json.
// A hash checks integrity, not publisher authentication or threshold endorsement.
const snapshot = publication.snapshot;
const v3=snapshot.profile==='aidmesh/approved-registry/v3-holder-value';
if (publication.digest !== sha(snapshot)) throw Error('snapshot digest mismatch');
const p = await buildPoseidon();
const tags = Object.fromEntries(Object.entries(JSON.parse(readFileSync('zk/domain_tags.json'))
  .tags).map(([k,v]) => [k, v.value]));
const hash = values => p.F.toObject(p(values.map(BigInt))).toString();
const depth = 20;
const tree = (leaves, tag) => {
  if (!leaves.length || leaves.length > 2 ** depth) throw Error('invalid tree size');
  const levels = [leaves];
  for (let level=0; level<depth; level++) {
    const row = levels[level], next = [];
    for(let i=0;i<row.length;i+=2) next.push(hash([tag,row[i],row[i+1] ?? '0']));
    levels.push(next);
  }
  return {root: levels[depth][0], paths: leaves.map((_, index) => {
    let cursor=index; const elements=[], indices=[];
    for(let level=0;level<depth;level++) {
      indices.push(cursor % 2); elements.push(levels[level][cursor ^ 1] ?? '0');
      cursor=Math.floor(cursor/2);
    }
    return {elements, indices};
  })};
};
if(!['aidmesh/approved-registry/v1','aidmesh/approved-registry/v2','aidmesh/approved-registry/v3-holder-value'].includes(snapshot.profile))
  throw Error('unknown profile');
const admissions = new Map(snapshot.admissions.map(a => [a.id,a.statement]));
if(admissions.size !== snapshot.admissions.length) throw Error('duplicate admissions');
const totals = new Map(), holderIndices = new Set(), counts = new Map();
for (const {statement:s} of snapshot.admissions) {
  for (const k of ['campaign','epoch','deployment_context'])
    if(String(s[k]) !== String(snapshot.policy[k])) throw Error('admission policy mismatch');
  const key=canonical([s.cell,s.group,s.category]);
  const q=BigInt(s.quota);
  if(q<=0n || q>=2n**64n) throw Error('invalid quota');
  totals.set(key,(totals.get(key)??0n)+q);
}
for (const [key,total] of totals) {
  const allocation=snapshot.policy.allocations.find(a=>canonical([a.cell,a.group,a.category])===key);
  if(!allocation || total>BigInt(v3?allocation.entitlements.length:allocation.quota)) throw Error('aggregate quota exceeds allocation');
}
const credentials = snapshot.admissions.map(({statement:s}) => hash([tags.credential_leaf,
  s.commitment,s.campaign,s.epoch,s.cell,s.group,s.category,s.quota,s.deployment_context]));
const assignedSerials=new Set();
const v3Tag=JSON.parse(readFileSync("zk/domain_tags_v3.json")).entitlement_holder_leaf;
const seen = new Map(), entitlementOrder=[], entitlementBindings=[], entitlements=[];
for (const e of snapshot.entitlements) {
  const s = admissions.get(e.admission);
  if(!s || ['cell','group','category'].some(k => String(s[k]) !== String(e[k]))
      || BigInt(e.index) < 1n || BigInt(e.index) > BigInt(s.quota))
    throw Error('entitlement not bound to approved credential');
  const key = canonical(v3?[e.admission,e.index]:[e.cell,e.group,e.category,e.index]);
  const holderKey=canonical([e.admission,e.index]);
  if(holderIndices.has(holderKey)) throw Error('duplicate holder index');
  holderIndices.add(holderKey); counts.set(e.admission,(counts.get(e.admission)??0n)+1n);
  const allocation = snapshot.policy.allocations.find(a =>
    ['cell','group','category'].every(k => String(a[k]) === String(e[k])));
  if(v3) {
    const serialKey=canonical([e.cell,e.group,e.category,e.serial]);
    const issued=snapshot.admissions.find(a=>a.id===e.admission);
    if(!allocation || assignedSerials.has(serialKey) || String(e.commitment)!==String(s.commitment)
      || !allocation.entitlements.some(a=>String(a.serial)===String(e.serial)&&String(a.value)===String(e.value))
      || String(issued.serials[Number(e.index)-1])!==String(e.serial)) throw Error('invalid value/owner/serial assignment');
    assignedSerials.add(serialKey);
  } else if(!allocation || String(allocation.value) !== String(e.value)) throw Error('wrong allocation value');
  const leaf=hash([v3?v3Tag:tags.entitlement_leaf,s.campaign,s.epoch,e.cell,e.group,e.category,
    e.index,e.value,s.deployment_context,...(v3?[s.commitment]:[])]);
  let position=seen.get(key);
  if(position===undefined) {
    position=entitlements.length; seen.set(key,position);
    entitlements.push(leaf); entitlementOrder.push(e);
  } else if(entitlements[position]!==leaf) throw Error('conflicting value for shared index');
  entitlementBindings.push({admission:e.admission,index:e.index,pathIndex:position});
}
for(const [id,s] of admissions)
  if(counts.get(id)!==BigInt(s.quota)) throw Error('missing issued holder indices');
writeFileSync(output,JSON.stringify({profile:v3?'aidmesh/approved-p1-roots/v3-holder-value':'aidmesh/approved-p1-roots/v1',
  snapshotDigest:publication.digest,credential:tree(credentials,tags.credential_merkle_node),
  entitlement:tree(entitlements,tags.entitlement_merkle_node),
  admissionOrder:snapshot.admissions.map(a=>a.id),entitlementOrder,entitlementBindings,
  scope:'approved snapshot trees; root endorsement and gateway integration required'},null,2));
