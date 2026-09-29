import {zKey} from 'snarkjs';
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import {randomBytes,createHash} from 'node:crypto';
const build='zk/build_v3', circuit=`${build}/aidmesh_voucher_v3.r1cs`, ptau='zk/public/powersOfTau28_hez_final_15.ptau';
if(existsSync(`${build}/build_manifest.json`))throw Error('completed build exists; use a new version directory');
const start=Date.now();
console.log('Creating v3 circuit-specific development key');
await zKey.newZKey(circuit,ptau,`${build}/initial.zkey`);
await zKey.contribute(`${build}/initial.zkey`,`${build}/aidmesh_dev_final.zkey`,
  'single-operator development contribution',randomBytes(64).toString('hex'));
if(!await zKey.verifyFromR1cs(circuit,ptau,`${build}/aidmesh_dev_final.zkey`))throw Error('key verification failed');
writeFileSync(`${build}/verification_key.json`,JSON.stringify(await zKey.exportVerificationKey(`${build}/aidmesh_dev_final.zkey`),null,2));
writeFileSync(`${build}/AidMeshGroth16Verifier.sol`,await zKey.exportSolidityVerifier(`${build}/aidmesh_dev_final.zkey`,
  {groth16:readFileSync('node_modules/snarkjs/templates/verifier_groth16.sol.ejs','utf8')}));
const files=[circuit,ptau,`${build}/aidmesh_dev_final.zkey`,`${build}/verification_key.json`,`${build}/AidMeshGroth16Verifier.sol`];
writeFileSync(`${build}/build_manifest.json`,JSON.stringify({scope:'v3 development SRS, one operator; not a production ceremony',
  elapsedSeconds:(Date.now()-start)/1000,sha256:Object.fromEntries(files.map(f=>[f,createHash('sha256').update(readFileSync(f)).digest('hex')]))},null,2));
console.log('v3 key and verifier verified');process.exit(0);
