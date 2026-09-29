// Fresh P1 proof under newly built approved roots; synthetic secret fixture only.
import {readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {groth16} from 'snarkjs';
import {buildPoseidon} from 'circomlibjs';
const [directory] = process.argv.slice(2);
const enrollment = JSON.parse(readFileSync('results/original_pillars_20260906/coconut/p1_admission_witness.json'));
const input = JSON.parse(readFileSync(enrollment.input_path));
const roots = JSON.parse(readFileSync(join(directory,'roots.json')));
const position=roots.entitlementOrder.findIndex(e=>String(e.index)===String(input.voucherIndex));
if(position<0) throw Error('voucher not issued');
input.credentialRoot=roots.credential.root;
input.credentialPathElements=roots.credential.paths[0].elements;
input.credentialPathIndices=roots.credential.paths[0].indices;
input.entitlementRoot=roots.entitlement.root;
input.entitlementPathElements=roots.entitlement.paths[position].elements;
input.entitlementPathIndices=roots.entitlement.paths[position].indices;
input.value=String(roots.entitlementOrder[position].value);
const p=await buildPoseidon();
const tags=Object.fromEntries(Object.entries(JSON.parse(readFileSync('zk/domain_tags.json'))
  .tags).map(([k,v])=>[k,v.value]));
const hash=values=>p.F.toObject(p(values.map(BigInt))).toString();
const context=hash([tags.deployment_context,...['chainId','gatewayAddress','settlementAddress',
  'headerHash','policyHash','committeeHash'].map(k=>input[k])]);
input.nullifier=hash([tags.nullifier,input.secret,input.campaignId,input.epoch,input.riskCell,
  input.category,input.voucherIndex,input.value,context]);
const started=performance.now();
const {proof,publicSignals}=await groth16.fullProve(input,
  'zk/build/aidmesh_voucher_js/aidmesh_voucher.wasm','zk/build/aidmesh_dev_final.zkey');
const elapsed=performance.now()-started;
const vk=JSON.parse(readFileSync('zk/build/verification_key.json'));
if(!await groth16.verify(vk,publicSignals,proof)) throw Error('fresh proof invalid');
const mutated=[...publicSignals]; mutated[5]=(BigInt(mutated[5])+1n).toString();
if(await groth16.verify(vk,mutated,proof)) throw Error('mutated root accepted');
writeFileSync(join(directory,'fresh_proof.json'),JSON.stringify(proof,null,2));
writeFileSync(join(directory,'fresh_public.json'),JSON.stringify(publicSignals,null,2));
writeFileSync(join(directory,'proof_manifest.json'),JSON.stringify({freshProof:true,
  verified:true,mutatedCredentialRootRejected:true,proofMilliseconds:elapsed,
  scope:'fresh P1 membership proof after Coconut admission; synthetic enrollment and allocation',
  remaining:['threshold root endorsement','BPv7 gateway settlement','allocator linkage']},null,2));
console.log('Fresh approved-root P1 proof verified; root mutation rejected.');
process.exit(0);
