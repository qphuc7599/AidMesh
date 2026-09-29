import {readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {buildPoseidon} from 'circomlibjs';
import {WitnessCalculatorBuilder} from 'circom_runtime';
const out=process.argv[2],build='zk/build_v3';
const calculator=await WitnessCalculatorBuilder(readFileSync(`${build}/aidmesh_voucher_v3_js/aidmesh_voucher_v3.wasm`));
const {base,beneficiaries}=JSON.parse(readFileSync(join(out,'synthetic_private_enrollment.json')));
const {snapshot}=JSON.parse(readFileSync(join(out,'root_input.json')));
const roots=JSON.parse(readFileSync(join(out,'roots.json')));
const p=await buildPoseidon();
const tags=Object.fromEntries(Object.entries(JSON.parse(readFileSync('zk/domain_tags.json')).tags).map(([k,v])=>[k,v.value]));
const hash=v=>p.F.toObject(p(v.map(BigInt))).toString();
const inputs=beneficiaries.map(b=>{
  const a=snapshot.admissions.find(a=>a.statement.commitment===b.statement.commitment);
  const ci=roots.admissionOrder.indexOf(a.id);
  const ei=roots.entitlementBindings.find(e=>e.admission===a.id).pathIndex;
  const input={...base,secret:b.secret,secretBlinding:b.secretBlinding,groupCode:b.statement.group,
    quota:b.statement.quota,voucherIndex:'1',value:String(roots.entitlementOrder[ei].value),
    credentialRoot:roots.credential.root,entitlementRoot:roots.entitlement.root,
    credentialPathElements:roots.credential.paths[ci].elements,credentialPathIndices:roots.credential.paths[ci].indices,
    entitlementPathElements:roots.entitlement.paths[ei].elements,entitlementPathIndices:roots.entitlement.paths[ei].indices,
    fallbackMode:'0',fallbackWindow:'0',fallbackTag:'0'};
  input.nullifier=hash([tags.nullifier,input.secret,input.campaignId,input.epoch,input.riskCell,
    input.category,input.voucherIndex,input.value,b.statement.deployment_context]);
  return input;
});
const rows=[];
for(let i=0;i<inputs.length;i++) {
  const input=inputs[i],other=inputs[(i+1)%inputs.length];
  await calculator.calculateWTNSBin(input);
  const swapped={...input,value:other.value,entitlementPathElements:other.entitlementPathElements,
    entitlementPathIndices:other.entitlementPathIndices};
  // Recompute the attacker's valid nullifier for the stolen value: rejection
  // must come from owner-bound entitlement membership, not a stale nullifier.
  const context=beneficiaries[i].statement.deployment_context;
  swapped.nullifier=hash([tags.nullifier,input.secret,input.campaignId,input.epoch,input.riskCell,
    input.category,input.voucherIndex,swapped.value,context]);
  const mutations={foreign_holder_entitlement:swapped,
    changed_value:{...input,value:String(BigInt(input.value)+1n)},
    unissued_index:{...input,voucherIndex:String(BigInt(input.quota)+1n)},changed_campaign:{...input,campaignId:String(BigInt(input.campaignId)+1n)},
    changed_secret:{...input,secret:String(BigInt(input.secret)+1n)},
    credential_path_swap:{...input,credentialPathElements:other.credentialPathElements,
      credentialPathIndices:other.credentialPathIndices}};
  for(const [name,witness] of Object.entries(mutations)) {
    let rejected=false;try{await calculator.calculateWTNSBin(witness);}catch{rejected=true;}
    if(!rejected)throw Error(`accepted ${name}`);
    rows.push({holder:i,attack:name,rejected});
  }
}
writeFileSync(join(out,'private_wire_controls.json'),JSON.stringify({valid:inputs.length,rows,
  scope:'real v3 circuit witness checks; foreign-leaf attack recomputes its nullifier'},null,2));
console.log(`${rows.length} invalid private witnesses rejected`);process.exit(0);
