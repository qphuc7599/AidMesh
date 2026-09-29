"""Deploy the final v3 verifier/gateway/settlement before generating bound proofs."""
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.experiments.semantic_e2e_campaign import _Harness

out=Path(sys.argv[1]);rpc=sys.argv[2];verifier=Path(sys.argv[3]).resolve();cap=int(sys.argv[4])
h=_Harness(ROOT,rpc_url=rpc,verifier_source=verifier);h.witnesses=(*h.accounts[1:4],h.accounts[9])
committee=int(h.gateway.functions.semanticCommitteeHash(1,3,cap,list(h.witnesses)).call())
deployment=dict(verifier=h.verifier.address,gateway=h.gateway.address,settlement=h.settlement.address,
    deploymentGas=h.deployment_gas,chainId=h.web3.eth.chain_id,client=h.web3.client_version,
    blockGasLimit=int(h.web3.eth.get_block('latest').gasLimit),witnesses=h.witnesses,sites=h.sites,
    threshold=3,cap=cap,verifierSha256=hashlib.sha256(verifier.read_bytes()).hexdigest())
(out/'deployment.json').write_text(json.dumps(deployment,indent=2))
config=dict(beneficiaries=[['1',0] for _ in range(100)],values=[1+(i%2) for i in range(100)],
    baseOverrides=dict(chainId=str(h.web3.eth.chain_id),gatewayAddress=str(int(h.gateway.address,16)),
        settlementAddress=str(int(h.settlement.address,16)),committeeHash=str(committee)))
(out/'configuration.json').write_text(json.dumps(config,indent=2))
print(json.dumps(deployment,indent=2))
