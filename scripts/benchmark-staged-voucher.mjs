// Credential already admitted. Measure selection/derivation/proving/serialization,
// then deserialize/P1/root-context/certificate checks. No remote issuance latency.
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash,generateKeyPairSync,sign,verify} from 'node:crypto';
import {buildPoseidon} from 'circomlibjs';
import {groth16} from 'snarkjs';
import {WitnessCalculatorBuilder} from 'circom_runtime';
const [directory,optimization]=process.argv.slice(2);mkdirSync(directory,{recursive:false});
const cached=optimization==='cached';
const wasm=cached?readFileSync('zk/build/aidmesh_voucher_js/aidmesh_voucher.wasm'):null;
const provingKey=cached?readFileSync('zk/build/aidmesh_dev_final.zkey'):null;
const calculator=cached?await WitnessCalculatorBuilder(wasm):null;
const source='results/comprehensive_revision_20260906/multi';
const {base,beneficiaries}=JSON.parse(readFileSync(join(source,'synthetic_private_enrollment.json')));
const roots=JSON.parse(readFileSync(join(source,'roots.json')));
const {snapshot}=JSON.parse(readFileSync(join(source,'root_input.json')));
const vk=JSON.parse(readFileSync('zk/build/verification_key.json'));
const tags=Object.fromEntries(Object.entries(JSON.parse(readFileSync('zk/domain_tags.json')).tags).map(([k,v])=>[k,v.value]));
const p=await buildPoseidon();const hash=v=>p.F.toObject(p(v.map(BigInt))).toString();
const keys=Array.from({length:4},()=>generateKeyPairSync('ed25519'));
const encode=values=>Buffer.concat([Buffer.from('AMV1'),...values.map(v=>{
  const n=BigInt(v);if(n<0n||n>=2n**256n)throw Error('noncanonical integer');
  return Buffer.from(n.toString(16).padStart(64,'0'),'hex');})]);
const decode=bytes=>{
  if(bytes.length!==4+25*32||bytes.subarray(0,4).toString()!=='AMV1')throw Error('binary profile');
  return Array.from({length:25},(_,i)=>BigInt('0x'+bytes.subarray(4+32*i,36+32*i).toString('hex')).toString());
};
writeFileSync(join(directory,'protocol.json'),JSON.stringify({trials:100,warmup:3,cachedWitnessCalculatorAndKey:cached,
  originalLimits:{generationMs:1000,verificationMs:100,voucherBytes:8192,certificateBytes:2048},
  profile:'staged P1 with cached approved roots and three individual Ed25519 witness signatures',
  caveat:'individual certificate not aggregate scheme; no complete direct Coconut-P1 privacy proof',
  runnerSha256:createHash('sha256').update(readFileSync(new URL(import.meta.url))).digest('hex')},null,2));
const rows=[];
for(let trial=-3;trial<100;trial++) {
  const started=performance.now();
  const beneficiary=beneficiaries[(trial+3)%beneficiaries.length],s=beneficiary.statement;
  const admission=snapshot.admissions.find(a=>a.statement.commitment===s.commitment);
  const cp=roots.admissionOrder.indexOf(admission.id),index=1+((trial+3)%Number(s.quota));
  const ep=roots.entitlementBindings.find(e=>e.admission===admission.id&&Number(e.index)===index).pathIndex;
  const input={...base,secret:beneficiary.secret,secretBlinding:beneficiary.secretBlinding,
    groupCode:s.group,quota:s.quota,voucherIndex:String(index),value:String(roots.entitlementOrder[ep].value),
    credentialRoot:roots.credential.root,entitlementRoot:roots.entitlement.root,
    credentialPathElements:roots.credential.paths[cp].elements,credentialPathIndices:roots.credential.paths[cp].indices,
    entitlementPathElements:roots.entitlement.paths[ep].elements,entitlementPathIndices:roots.entitlement.paths[ep].indices,
    fallbackMode:'0',fallbackWindow:'0',fallbackTag:'0'};
  input.nullifier=hash([tags.nullifier,input.secret,input.campaignId,input.epoch,input.riskCell,
    input.category,input.voucherIndex,input.value,s.deployment_context]);
  const {proof,publicSignals}=cached
    ?await groth16.prove(provingKey,await calculator.calculateWTNSBin(input))
    :await groth16.fullProve(input,
      'zk/build/aidmesh_voucher_js/aidmesh_voucher.wasm','zk/build/aidmesh_dev_final.zkey');
  const bytes=encode([...proof.pi_a.slice(0,2),...proof.pi_b[0],...proof.pi_b[1],...proof.pi_c.slice(0,2),...publicSignals]);
  const generationMs=performance.now()-started;
  const certificate=keys.slice(0,3).map(k=>sign(null,bytes,k.privateKey));
  const check=performance.now();
  const v=decode(bytes),pub=v.slice(8);
  const pi={pi_a:[...v.slice(0,2),'1'],pi_b:[v.slice(2,4),v.slice(4,6),['1','0']],
    pi_c:[...v.slice(6,8),'1'],protocol:'groth16',curve:'bn128'};
  if(pub[5]!==roots.credential.root||pub[6]!==roots.entitlement.root
      ||pub[0]!==base.campaignId||pub[1]!==base.epoch
      ||pub.slice(11).some((value,i)=>value!==String(base[['chainId','gatewayAddress','settlementAddress','headerHash','policyHash','committeeHash'][i]])))
    throw Error('context mismatch');
  if(!await groth16.verify(vk,pub,pi)||!certificate.every((sig,i)=>verify(null,bytes,keys[i].publicKey,sig)))
    throw Error('verification failed');
  const verificationMs=performance.now()-check;
  rows.push({trial,warmup:trial<0,generationMs,verificationMs,voucherBytes:bytes.length,
    certificateBytes:certificate.reduce((s,c)=>s+c.length,0)+3});
  writeFileSync(join(directory,'runs.json'),JSON.stringify(rows,null,2));
  if(trial>=0&&trial%20===0)console.log(`Completed ${trial+1}/100`);
}
const measured=rows.filter(r=>!r.warmup);
const median=k=>{const a=measured.map(r=>r[k]).sort((a,b)=>a-b);return(a[49]+a[50])/2;};
const summary={trials:100,generationMedianMs:median('generationMs'),verificationMedianMs:median('verificationMs'),
  voucherBytes:median('voucherBytes'),certificateBytes:median('certificateBytes'),
  fullOriginalH5Established:false,scope:'staged cached-root profile, individual signatures, single warm process'};
summary.generationBudgetMet=summary.generationMedianMs<=1000;
summary.verificationBudgetMet=summary.verificationMedianMs<=100;
writeFileSync(join(directory,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));process.exit(0);
