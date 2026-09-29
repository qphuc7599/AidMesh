import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {groth16} from 'snarkjs';

const [source,output]=process.argv.slice(2);
if(!source||!output)throw Error('source and output required');
const rows=JSON.parse(readFileSync(`${source}/fresh_proofs.json`));
const vk=JSON.parse(readFileSync('zk/build_v3/verification_key.json'));
const measured=[];
// Warm the verifier with a real proof but do not include it in the 100 trials.
if(!await groth16.verify(vk,rows[0].publicSignals,rows[0].proof))throw Error('warmup failed');
for(let i=0;i<rows.length;i++){
  const row=rows[i],start=performance.now();
  const valid=await groth16.verify(vk,row.publicSignals,row.proof);
  const verificationMs=performance.now()-start;
  if(!valid)throw Error(`invalid proof ${i}`);
  const wire=Buffer.from(JSON.stringify({proof:row.proof,publicSignals:row.publicSignals}));
  measured.push({trial:i,verificationMs,proofGenerationMs:row.milliseconds,
    proofWireBytes:wire.length,statementSha256:createHash('sha256').update(wire).digest('hex')});
}
writeFileSync(output,JSON.stringify({profile:'warm P1-v3 verification of final admitted proofs',rows:measured},null,2));
console.log(`${measured.length} final proofs benchmarked`);
