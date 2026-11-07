import io
import zipfile

from edinet import taxonomy
from tests_py.test_ingest_xbrl import LABELS

PAGES = {
    "/search/index.html": '<a href="/search/20251111.html">a</a><a href="/search/20260911.html">b</a>',
    "/search/20251111.html": '<title>2026年版EDINETタクソノミの公表について</title><a href="/search/20251111/1c_Taxonomy.zip">',
    "/search/20260911.html": '<title>2027年版EDINETタクソノミ（案）の公表について</title><a href="/search/20260911/1c_Taxonomy.zip">',
}


def test_list_releases_skips_drafts(monkeypatch):
    monkeypatch.setattr(taxonomy, "PAST_PAGES", ("20250000",))
    PAGES["/search/20250000.html"] = '<title>2025年版EDINETタクソノミの公表について</title><a href="/search/20250000/1c.zip">'
    monkeypatch.setattr(taxonomy, "fetch", lambda url: PAGES[url.removeprefix(taxonomy.BASE)].encode())
    assert taxonomy.list_releases() == [
        ("2025", "https://www.fsa.go.jp/search/20250000/1c.zip"),
        ("2026", "https://www.fsa.go.jp/search/20251111/1c_Taxonomy.zip"),
    ]


def test_release_labels():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("t/taxonomy/jpcrp/2025-11-01/label/jpcrp_2025-11-01_lab.xml", LABELS)
        zf.writestr("t/samples/x_lab.xml", LABELS)
    assert taxonomy.release_labels(buf.getvalue()) == [
        ("jpcrp/2025-11-01", "jpcrp030000-asr_E00000-000:FooSegmentMember", "http://www.xbrl.org/2003/role/label", "ja", "フー事業")
    ]
