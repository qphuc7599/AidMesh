-------------------- MODULE AidMeshQuantitativeFrontier --------------------
EXTENDS Integers

(*
Exhaustive finite-instance check of the quantitative placement identities used
by the manuscript.  Two sites are sufficient to exercise both misplacement
directions, calibrated placement, relocation, and fail-closed stranding.  The
algebraic theorem itself is parameterized over any finite number of sites.
*)

CONSTANTS MaxValue, EnforceConservation

VARIABLES b0, b1, bp0, bp1, d0, d1

vars == <<b0, b1, bp0, bp1, d0, d1>>
Values == 0..MaxValue

Min(a, b) == IF a <= b THEN a ELSE b
Abs(x) == IF x >= 0 THEN x ELSE -x

B == b0 + b1
BP == bp0 + bp1
D == d0 + d1
Stranded == B - BP

StaticCompletion == Min(b0, d0) + Min(b1, d1)
DeliveredCompletion == Min(bp0, d0) + Min(bp1, d1)
OracleCompletion == Min(B, D)
StaticL1 == Abs(b0 - d0) + Abs(b1 - d1)
DeliveredL1 == Abs(bp0 - d0) + Abs(bp1 - d1)

Init ==
    /\ b0 \in Values
    /\ b1 \in Values
    /\ bp0 \in Values
    /\ bp1 \in Values
    /\ d0 \in Values
    /\ d1 \in Values
    /\ (~EnforceConservation \/ BP <= B)

Next == UNCHANGED vars

TypeOK ==
    /\ b0 \in Values /\ b1 \in Values
    /\ bp0 \in Values /\ bp1 \in Values
    /\ d0 \in Values /\ d1 \in Values

NoCapacityMint == BP <= B

StaticPlacementIdentity ==
    2 * StaticCompletion = B + D - StaticL1

OracleGapIdentity ==
    2 * (OracleCompletion - StaticCompletion) =
        StaticL1 - Abs(B - D)

RelocationGainIdentity ==
    2 * (DeliveredCompletion - StaticCompletion) =
        StaticL1 - DeliveredL1 - Stranded

RelocationCriterion ==
    (DeliveredCompletion > StaticCompletion) <=>
        (StaticL1 - DeliveredL1 > Stranded)

Spec == Init /\ [][Next]_vars
=============================================================================
