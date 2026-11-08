from edinet.xbrl_facts import parse_instance

INSTANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:jppfs_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"
  xmlns:jpcrp030000-asr_E00001-000="http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E00001-000">
  <xbrli:context id="CurrentYearDuration_NonConsolidatedMember">
    <xbrli:entity><xbrli:identifier scheme="x">E00001-000</xbrli:identifier>
      <xbrli:segment><xbrldi:explicitMember dimension="jppfs_cor:ConsolidatedOrNonConsolidatedAxis">jppfs_cor:NonConsolidatedMember</xbrldi:explicitMember></xbrli:segment>
    </xbrli:entity>
    <xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="CurrentYearInstant">
    <xbrli:entity><xbrli:identifier scheme="x">E00001-000</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
  <jppfs_cor:NetSales contextRef="CurrentYearDuration_NonConsolidatedMember" unitRef="JPY" decimals="-6">1000000</jppfs_cor:NetSales>
  <jpcrp030000-asr_E00001-000:Original contextRef="CurrentYearInstant" unitRef="JPY" decimals="0" xsi:nil="true"/>
</xbrli:xbrl>"""


def test_parse_instance():
    namespaces, contexts, facts = parse_instance(INSTANCE)
    assert namespaces["jppfs_cor"] == "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"
    assert contexts["CurrentYearDuration_NonConsolidatedMember"].members == (
        ("jppfs_cor:ConsolidatedOrNonConsolidatedAxis", "jppfs_cor:NonConsolidatedMember"),
    )
    assert contexts["CurrentYearInstant"].instant == "2026-03-31"
    assert [(f.element_id, f.decimals, f.is_nil, f.value) for f in facts] == [
        ("jppfs_cor:NetSales", "-6", False, "1000000"),
        ("jpcrp030000-asr_E00001-000:Original", "0", True, None),
    ]
