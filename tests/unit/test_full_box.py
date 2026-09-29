from fractions import Fraction as F
from aidmesh.allocator.progressive_fill import PackingProblem,progressive_fill
from aidmesh.allocator.full_box import lexicographic_full_box


def problem():
    return PackingProblem(cell='review',demand={'g1':{'a':1,'b':1},'g2':{'a':1,'b':0}},
        stock={'a':1,'b':1},weights={'g1':1,'g2':1},conversion_factors={'a':1,'b':1},
        directions={'g1':{'a':F(1,2),'b':F(1,2)},'g2':{'a':1,'b':0}})


def test_review_counterexample_full_box_and_committed():
    p=problem()
    for utility_first in (False,True):
        optimum=lexicographic_full_box(p,utility_first=utility_first)
        assert min(optimum.ratios.values())==F(2,3)
        assert optimum.utility==2
        assert optimum.certificates
    floor=progressive_fill(p)
    result=lexicographic_full_box(p,floors=floor.amounts)
    assert min(result.ratios.values())==F(1,2)
    assert result.utility==2
    assert all(result.amounts[g][k]>=floor.amounts[g][k] for g in floor.groups for k in floor.categories)


def test_weighted_utility_priority_is_distinct():
    p=problem(); u={'g1':{'a':1,'b':1},'g2':{'a':2,'b':2}}
    result=lexicographic_full_box(p,utility_first=True,utilities=u)
    assert result.utility==3
    assert result.amounts['g2']['a']==1
    assert min(result.ratios.values())==F(1,2)


def test_delivery_factor_compensates_low_connectivity():
    p=problem();baseline=lexicographic_full_box(p)
    adjusted=lexicographic_full_box(p,service_factors={'g1':F(1,2),'g2':1})
    assert adjusted.amounts['g1']['a']>=baseline.amounts['g1']['a']
    assert min(adjusted.ratios.values())==F(2,5)
