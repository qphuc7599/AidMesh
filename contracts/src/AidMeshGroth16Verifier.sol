// SPDX-License-Identifier: GPL-3.0
/*
    Copyright 2021 0KIMS association.

    This file is generated with [snarkJS](https://github.com/iden3/snarkjs).

    snarkJS is a free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    snarkJS is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
    or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
    License for more details.

    You should have received a copy of the GNU General Public License
    along with snarkJS. If not, see <https://www.gnu.org/licenses/>.
*/

pragma solidity 0.8.36;

contract Groth16Verifier {
    // Scalar field size
    uint256 constant r    = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    // Base field size
    uint256 constant q   = 21888242871839275222246405745257275088696311157297823662689037894645226208583;

    // Verification Key data
    uint256 constant alphax  = 14996063214807350131823558629447079629474328874930302436413580926835878144932;
    uint256 constant alphay  = 18258304401927927860668918869441878104698697765060986626574006676030170105913;
    uint256 constant betax1  = 19815260811497673957479983736100379863534505708554713877697848850388122314107;
    uint256 constant betax2  = 20610060193362464754482425863951520974402108803237837575316167278334582462299;
    uint256 constant betay1  = 3298721256647747071614570035534186702519234323927309631063500289776824503652;
    uint256 constant betay2  = 3348563321430169441008838771428960347893581189046159631855321271696172257509;
    uint256 constant gammax1 = 11559732032986387107991004021392285783925812861821192530917403151452391805634;
    uint256 constant gammax2 = 10857046999023057135944570762232829481370756359578518086990519993285655852781;
    uint256 constant gammay1 = 4082367875863433681332203403145435568316851327593401208105741076214120093531;
    uint256 constant gammay2 = 8495653923123431417604973247489272438418190587263600148770280649306958101930;
    uint256 constant deltax1 = 11559732032986387107991004021392285783925812861821192530917403151452391805634;
    uint256 constant deltax2 = 10857046999023057135944570762232829481370756359578518086990519993285655852781;
    uint256 constant deltay1 = 4082367875863433681332203403145435568316851327593401208105741076214120093531;
    uint256 constant deltay2 = 8495653923123431417604973247489272438418190587263600148770280649306958101930;

    
    uint256 constant IC0x = 11947619358131137229198039730813676975230359312676485324130692345506664248808;
    uint256 constant IC0y = 20127162646329215701726686961542698919323623514643051745004793793696032464706;
    
    uint256 constant IC1x = 2288813663060030112457929477690147236746022559421092808073360644463201169498;
    uint256 constant IC1y = 17553250068930644112044624914961683997042646669981685898820342374606039852033;
    
    uint256 constant IC2x = 20091145642484950503280148567327274035131116473595294673239898554607948165786;
    uint256 constant IC2y = 13330837369963646540437940935193113329654000898659283474442869833887555125750;
    
    uint256 constant IC3x = 4662255000505276104524242029050769661275167007908484563072362041180593004896;
    uint256 constant IC3y = 10120912249780126432639625596973163428603043471067192068483994312240008891672;
    
    uint256 constant IC4x = 4712922274558075140084335572068337093199033945632495470312445814678609928339;
    uint256 constant IC4y = 7133336598967649382593952445323034257857114134646513572856073694099864062989;
    
    uint256 constant IC5x = 20796352791745370698597956194945803168725316154566275425912292661374153852073;
    uint256 constant IC5y = 1583144534993602294193065543203219018602230434572792321970778210705110913071;
    
    uint256 constant IC6x = 1812699934853024284718574497903329088597227284007129200289976514996931216127;
    uint256 constant IC6y = 4313952897262295770476649829007283707178712409076123433622168335743150498038;
    
    uint256 constant IC7x = 19483788613379630493633714238648397757068345125673019302216734804171940692480;
    uint256 constant IC7y = 21278370946470652913671810448173003740692126957781224040617712108519840057336;
    
    uint256 constant IC8x = 15481363298583227229250605895471031716772692762615553926612674679439907602953;
    uint256 constant IC8y = 4919261053522563405787448855425355725970368512351847646579323138426001086342;
    
    uint256 constant IC9x = 11547850021473462268553756912653566983434596995385521464593920837219479831810;
    uint256 constant IC9y = 18188153933807212589202647389024617457332256314889114056518620667244930948968;
    
    uint256 constant IC10x = 10325680408987389087586529693508872439922572813794473595543754277991617755047;
    uint256 constant IC10y = 15962887084010045468366844938830820953573409978002097216319327289363531271376;
    
    uint256 constant IC11x = 4415546495363900386826666756447737260786314081167175859213638815641535708754;
    uint256 constant IC11y = 5265469543519314955611950213849425415018688199055542911947675235389338991097;
    
    uint256 constant IC12x = 5873838770563408681661198363379950203725014644021250950660694467895433434601;
    uint256 constant IC12y = 9605705917290254979312349711016518152698244733114486451835011980019677674938;
    
    uint256 constant IC13x = 20157241056255372570195709571714869768193759484021421578042665626868316130916;
    uint256 constant IC13y = 19689465572805937172309023016259791787591123514817908246701223430722292676624;
    
    uint256 constant IC14x = 13551615807839883663820382008295759610762988506777824639942334766045423327697;
    uint256 constant IC14y = 7530959867561709926066772193534051041816198799255543992442073408718780650961;
    
    uint256 constant IC15x = 19264530507185121524083326443751998731139131272874734840913461301296415998964;
    uint256 constant IC15y = 20367991999620453559723229665660449961475711350806688522133525134744566276415;
    
    uint256 constant IC16x = 3420496352419194912889106623983698831125470239061830305518356426645062436748;
    uint256 constant IC16y = 9168937330829644928029320579584068961971945899089126099482369892136644178986;
    
    uint256 constant IC17x = 3803470649507388899842003823253867489071456228691385493541316649193025952352;
    uint256 constant IC17y = 9433194946635651572123231879619005953462013964596454851762735522498493451778;
    
 
    // Memory data
    uint16 constant pVk = 0;
    uint16 constant pPairing = 128;

    uint16 constant pLastMem = 896;

    function verifyProof(uint[2] calldata _pA, uint[2][2] calldata _pB, uint[2] calldata _pC, uint[17] calldata _pubSignals) public view returns (bool) {
        assembly {
            function checkField(v) {
                if iszero(lt(v, r)) {
                    mstore(0, 0)
                    return(0, 0x20)
                }
            }
            
            // G1 function to multiply a G1 value(x,y) to value in an address
            function g1_mulAccC(pR, x, y, s) {
                let success
                let mIn := mload(0x40)
                mstore(mIn, x)
                mstore(add(mIn, 32), y)
                mstore(add(mIn, 64), s)

                success := staticcall(sub(gas(), 2000), 7, mIn, 96, mIn, 64)

                if iszero(success) {
                    mstore(0, 0)
                    return(0, 0x20)
                }

                mstore(add(mIn, 64), mload(pR))
                mstore(add(mIn, 96), mload(add(pR, 32)))

                success := staticcall(sub(gas(), 2000), 6, mIn, 128, pR, 64)

                if iszero(success) {
                    mstore(0, 0)
                    return(0, 0x20)
                }
            }

            function checkPairing(pA, pB, pC, pubSignals, pMem) -> isOk {
                let _pPairing := add(pMem, pPairing)
                let _pVk := add(pMem, pVk)

                mstore(_pVk, IC0x)
                mstore(add(_pVk, 32), IC0y)

                // Compute the linear combination vk_x
                
                g1_mulAccC(_pVk, IC1x, IC1y, calldataload(add(pubSignals, 0)))
                
                g1_mulAccC(_pVk, IC2x, IC2y, calldataload(add(pubSignals, 32)))
                
                g1_mulAccC(_pVk, IC3x, IC3y, calldataload(add(pubSignals, 64)))
                
                g1_mulAccC(_pVk, IC4x, IC4y, calldataload(add(pubSignals, 96)))
                
                g1_mulAccC(_pVk, IC5x, IC5y, calldataload(add(pubSignals, 128)))
                
                g1_mulAccC(_pVk, IC6x, IC6y, calldataload(add(pubSignals, 160)))
                
                g1_mulAccC(_pVk, IC7x, IC7y, calldataload(add(pubSignals, 192)))
                
                g1_mulAccC(_pVk, IC8x, IC8y, calldataload(add(pubSignals, 224)))
                
                g1_mulAccC(_pVk, IC9x, IC9y, calldataload(add(pubSignals, 256)))
                
                g1_mulAccC(_pVk, IC10x, IC10y, calldataload(add(pubSignals, 288)))
                
                g1_mulAccC(_pVk, IC11x, IC11y, calldataload(add(pubSignals, 320)))
                
                g1_mulAccC(_pVk, IC12x, IC12y, calldataload(add(pubSignals, 352)))
                
                g1_mulAccC(_pVk, IC13x, IC13y, calldataload(add(pubSignals, 384)))
                
                g1_mulAccC(_pVk, IC14x, IC14y, calldataload(add(pubSignals, 416)))
                
                g1_mulAccC(_pVk, IC15x, IC15y, calldataload(add(pubSignals, 448)))
                
                g1_mulAccC(_pVk, IC16x, IC16y, calldataload(add(pubSignals, 480)))
                
                g1_mulAccC(_pVk, IC17x, IC17y, calldataload(add(pubSignals, 512)))
                

                // -A
                mstore(_pPairing, calldataload(pA))
                mstore(add(_pPairing, 32), mod(sub(q, calldataload(add(pA, 32))), q))

                // B
                mstore(add(_pPairing, 64), calldataload(pB))
                mstore(add(_pPairing, 96), calldataload(add(pB, 32)))
                mstore(add(_pPairing, 128), calldataload(add(pB, 64)))
                mstore(add(_pPairing, 160), calldataload(add(pB, 96)))

                // alpha1
                mstore(add(_pPairing, 192), alphax)
                mstore(add(_pPairing, 224), alphay)

                // beta2
                mstore(add(_pPairing, 256), betax1)
                mstore(add(_pPairing, 288), betax2)
                mstore(add(_pPairing, 320), betay1)
                mstore(add(_pPairing, 352), betay2)

                // vk_x
                mstore(add(_pPairing, 384), mload(add(pMem, pVk)))
                mstore(add(_pPairing, 416), mload(add(pMem, add(pVk, 32))))


                // gamma2
                mstore(add(_pPairing, 448), gammax1)
                mstore(add(_pPairing, 480), gammax2)
                mstore(add(_pPairing, 512), gammay1)
                mstore(add(_pPairing, 544), gammay2)

                // C
                mstore(add(_pPairing, 576), calldataload(pC))
                mstore(add(_pPairing, 608), calldataload(add(pC, 32)))

                // delta2
                mstore(add(_pPairing, 640), deltax1)
                mstore(add(_pPairing, 672), deltax2)
                mstore(add(_pPairing, 704), deltay1)
                mstore(add(_pPairing, 736), deltay2)


                let success := staticcall(sub(gas(), 2000), 8, _pPairing, 768, _pPairing, 0x20)

                isOk := and(success, mload(_pPairing))
            }

            let pMem := mload(0x40)
            mstore(0x40, add(pMem, pLastMem))

            // Validate that all evaluations ∈ F
            
            checkField(calldataload(add(_pubSignals, 0)))
            
            checkField(calldataload(add(_pubSignals, 32)))
            
            checkField(calldataload(add(_pubSignals, 64)))
            
            checkField(calldataload(add(_pubSignals, 96)))
            
            checkField(calldataload(add(_pubSignals, 128)))
            
            checkField(calldataload(add(_pubSignals, 160)))
            
            checkField(calldataload(add(_pubSignals, 192)))
            
            checkField(calldataload(add(_pubSignals, 224)))
            
            checkField(calldataload(add(_pubSignals, 256)))
            
            checkField(calldataload(add(_pubSignals, 288)))
            
            checkField(calldataload(add(_pubSignals, 320)))
            
            checkField(calldataload(add(_pubSignals, 352)))
            
            checkField(calldataload(add(_pubSignals, 384)))
            
            checkField(calldataload(add(_pubSignals, 416)))
            
            checkField(calldataload(add(_pubSignals, 448)))
            
            checkField(calldataload(add(_pubSignals, 480)))
            
            checkField(calldataload(add(_pubSignals, 512)))
            

            // Validate all evaluations
            let isValid := checkPairing(_pA, _pB, _pC, _pubSignals, pMem)

            mstore(0, isValid)
             return(0, 0x20)
         }
     }
 }
