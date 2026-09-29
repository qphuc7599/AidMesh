// Synthetic private fixtures for integration tests; never beneficiary data.
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {buildPoseidon} from 'circomlibjs';
const [out, configuration]=process.argv.slice(2);
const config=configuration?JSON.parse(readFileSync(configuration)):{};
if(!out) throw Error('output directory required');
mkdirSync(out,{recursive:true});
const reference=JSON.parse(readFileSync('results/original_pillars_20260906/coconut/p1_admission_witness.json'));
const base=JSON.parse(readFileSync(reference.input_path));
Object.assign(base,config.baseOverrides??{});
const p=await buildPoseidon();
const tags=Object.fromEntries(Object.entries(JSON.parse(readFileSync('zk/domain_tags.json')).tags)
  .map(([k,v])=>[k,v.value]));
const hash=values=>p.F.toObject(p(values.map(BigInt))).toString();
const context=hash([tags.deployment_context,...['chainId','gatewayAddress','settlementAddress',
  'headerHash','policyHash','committeeHash'].map(k=>base[k])]);
const beneficiaries=(config.beneficiaries??[['2',0],['3',0],['5',1]]).map(([quota,offset])=>{
  const secret=BigInt('0x'+randomBytes(30).toString('hex')).toString();
  const secretBlinding=BigInt('0x'+randomBytes(30).toString('hex')).toString();
  const group=(BigInt(base.groupCode)+BigInt(offset)).toString();
  return {secret,secretBlinding,statement:{commitment:hash([tags.secret_commitment,secret,secretBlinding]),
    campaign:base.campaignId,epoch:base.epoch,cell:base.riskCell,group,category:base.category,
    quota,deployment_context:context}};
});
writeFileSync(join(out,'synthetic_private_enrollment.json'),JSON.stringify({base,beneficiaries},null,2));
writeFileSync(join(out,'enrollment_public.json'),JSON.stringify(beneficiaries.map(b=>b.statement),null,2));
