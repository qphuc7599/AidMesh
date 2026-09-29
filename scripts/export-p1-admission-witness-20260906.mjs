// Export only enrollment-facing fields; never export the secret or its blinding.
import {readFileSync,writeFileSync} from 'node:fs';
import {buildPoseidon} from 'circomlibjs';
const [inputPath,outputPath]=process.argv.slice(2);
if(!inputPath||!outputPath) throw Error('expected P1 input path and output path');
const input=JSON.parse(readFileSync(inputPath,'utf8'));
const registry=JSON.parse(readFileSync('zk/domain_tags.json','utf8'));
const tags=Object.fromEntries(Object.entries(registry.tags).map(([k,v])=>[k,v.value]));
const poseidon=await buildPoseidon();
const hash=x=>poseidon.F.toObject(poseidon(x.map(BigInt))).toString();
const commitment=hash([tags.secret_commitment,input.secret,input.secretBlinding]);
const context=hash([tags.deployment_context,...['chainId','gatewayAddress','settlementAddress',
  'headerHash','policyHash','committeeHash'].map(k=>input[k])]);
const statement={commitment,campaign:input.campaignId,epoch:input.epoch,cell:input.riskCell,
  group:input.groupCode,category:input.category,quota:input.quota,deployment_context:context};
const leaf=hash([tags.credential_leaf,commitment,input.campaignId,input.epoch,input.riskCell,
  input.groupCode,input.category,input.quota,context]);
let current=leaf;
for(let i=0;i<input.credentialPathElements.length;i++) {
  const sibling=input.credentialPathElements[i];
  const bit=Number(input.credentialPathIndices[i]);
  if(bit!==0&&bit!==1) throw Error('nonbinary path');
  current=hash([tags.credential_merkle_node,...(bit?[sibling,current]:[current,sibling])]);
}
if(current!==String(input.credentialRoot)) throw Error('credential root mismatch');
writeFileSync(outputPath,JSON.stringify({statement,credential_leaf:leaf,credential_root:current,
  input_path:inputPath,scope:'synthetic cached proof witness; enrollment fields only'},null,2));
