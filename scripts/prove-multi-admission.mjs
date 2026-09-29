import {readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {buildPoseidon} from 'circomlibjs';
import {groth16} from 'snarkjs';
import {WitnessCalculatorBuilder} from 'circom_runtime';
const [out,selectedMode,build='zk/build',circuit='aidmesh_voucher',selectedWindow,
  outputName='fresh_proofs.json']=process.argv.slice(2);
const calculator=await WitnessCalculatorBuilder(readFileSync(join(build,`${circuit}_js`,`${circuit}.wasm`)));
const provingKey=readFileSync(join(build,'aidmesh_dev_final.zkey'));
const {base,beneficiaries}=JSON.parse(readFileSync(join(out,'synthetic_private_enrollment.json')));
const roots=JSON.parse(readFileSync(join(out,'roots.json')));
const {snapshot}=JSON.parse(readFileSync(join(out,'root_input.json')));
const p=await buildPoseidon();
const tags=Object.fromEntries(Object.entries(JSON.parse(readFileSync('zk/domain_tags.json')).tags)
  .map(([k,v])=>[k,v.value]));
const hash=values=>p.F.toObject(p(values.map(BigInt))).toString();
const vk=JSON.parse(readFileSync(join(build,'verification_key.json')));
const results=[];
for (const b of beneficiaries) {
  const admission=snapshot.admissions.find(a=>a.statement.commitment===b.statement.commitment);
  if(!admission) throw Error('unapproved beneficiary');
  const credentialPosition=roots.admissionOrder.indexOf(admission.id);
  // Prove every issued index, not only an easy first index.
  for(let index=1;index<=Number(b.statement.quota);index++) {
    const binding=roots.entitlementBindings.find(e=>e.admission===admission.id&&Number(e.index)===index);
    if(!binding) throw Error('unissued index');
    for(const mode of (selectedMode===undefined?[0,1]:[Number(selectedMode)])) {
      if(![0,1].includes(mode)) throw Error('invalid mode');
      const input={...base,secret:b.secret,secretBlinding:b.secretBlinding,
        groupCode:b.statement.group,quota:b.statement.quota,voucherIndex:String(index),
        value:String(roots.entitlementOrder[binding.pathIndex].value),
        credentialRoot:roots.credential.root,entitlementRoot:roots.entitlement.root,
        credentialPathElements:roots.credential.paths[credentialPosition].elements,
        credentialPathIndices:roots.credential.paths[credentialPosition].indices,
        entitlementPathElements:roots.entitlement.paths[binding.pathIndex].elements,
        entitlementPathIndices:roots.entitlement.paths[binding.pathIndex].indices,
        fallbackMode:String(mode),fallbackWindow:mode?String(selectedWindow??'1'):'0'};
      input.nullifier=hash([tags.nullifier,input.secret,input.campaignId,input.epoch,input.riskCell,
        input.category,input.voucherIndex,input.value,b.statement.deployment_context]);
      input.fallbackTag=mode?hash([tags.fallback_tag,input.secret,input.campaignId,input.epoch,
        input.riskCell,input.category,input.fallbackWindow,b.statement.deployment_context]):'0';
      const start=performance.now();
      const {proof,publicSignals}=await groth16.prove(provingKey,await calculator.calculateWTNSBin(input));
      const milliseconds=performance.now()-start;
      if(!await groth16.verify(vk,publicSignals,proof)) throw Error('invalid proof');
      const mutated=[...publicSignals];mutated[6]=(BigInt(mutated[6])+1n).toString();
      if(await groth16.verify(vk,mutated,proof)) throw Error('mutation accepted');
      results.push({admission:admission.id,index,mode,milliseconds,proof,publicSignals});
      writeFileSync(join(out,`${outputName}.partial`),JSON.stringify(results,null,2));
      if(results.length%10===0)console.log(`${results.length} fresh proofs verified`);
    }
  }
}
const unique=new Set(results.map(r=>r.publicSignals[7]));
if(unique.size!==beneficiaries.reduce((s,b)=>s+Number(b.statement.quota),0)) throw Error('beneficiary/index nullifier collision');
writeFileSync(join(out,outputName),JSON.stringify(results,null,2));
writeFileSync(join(out,`${outputName}.summary.json`),JSON.stringify({beneficiaries:beneficiaries.length,
  freshProofs:results.length,uniqueIssuedNullifiers:unique.size,rootMutationsRejected:results.length,
  scope:'synthetic allocator-to-Coconut-to-approved-roots-to-P1, requested paths, all issued indices'},null,2));
console.log(`${results.length} fresh proofs verified, ${unique.size} unique nullifiers.`);
process.exit(0);
