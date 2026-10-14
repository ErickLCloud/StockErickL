import json
from datetime import date

import pytest

from scripts import conferences as F

# the real row layout from MOPS t100sb02_1 (1303, 115/10/13), plus a header row and a 2330 row without slides
PAGE = """<table><tr><th>公司代號</th><th>公司名稱</th></tr>
<tr><td style='text-align:left !important;'>1303</td><td>南亞</td>
<td align='center'>115/10/13</td>
<td align='center'>15:00</td>
<td style='text-align:left !important;'>線上法說會</td>
<td style='text-align:left !important;'>本公司受邀參加摩根大通舉辦之法人說明會，說明本公司之營運概況&amp;展望</td>
<td style='text-align:left !important;'><a href='#' onclick='document.fm_fileDownload.fileName.value="130320261012M001.pdf";document.fm_fileDownload.submit();'><font color='blue'><u>130320261012M001.pdf</u></font></a></td>
<td style='text-align:left !important;'><a href='#' onclick='document.fm_fileDownload.fileName.value="130320261012E001.pdf";document.fm_fileDownload.submit();'><font color='blue'><u>130320261012E001.pdf</u></font></a></td>
<td style='text-align:left !important;'><a href='http://www.npc.com.tw' target='_blink'>http://www.npc.com.tw</a></td>
<td>&nbsp;</td><td>無</td><td align='center'><input type='button' value='查詢'></td></tr>
<tr><td>2330</td><td>台積電</td><td>115/10/15</td><td>14:00</td><td>線上法說會</td><td>公布第3季財務報告</td><td>&nbsp;</td><td>&nbsp;</td><td></td><td></td><td>無</td><td></td></tr>
</table>"""


def test_parse_month_reads_the_mops_row_layout():
    rows = F.parse_month(PAGE)
    assert [r["c"] for r in rows] == ["1303", "2330"]
    r = rows[0]
    assert r == {"c": "1303", "d": "2026-10-13", "t": "15:00", "p": "線上法說會",
                 "m": "本公司受邀參加摩根大通舉辦之法人說明會，說明本公司之營運概況&展望",
                 "zh": "130320261012M001.pdf", "en": "130320261012E001.pdf"}
    assert rows[1]["zh"] == "" and rows[1]["en"] == ""


def test_months_around_crosses_year_ends():
    assert F.months_around(date(2026, 10, 13)) == [(115, 8), (115, 9), (115, 10), (115, 11)]
    assert F.months_around(date(2027, 1, 5)) == [(115, 11), (115, 12), (116, 1), (116, 2)]


def test_build_keeps_newest_first_capped_and_records_errors(tmp_path):
    def fetch(market, y, m):
        if market == "otc":
            raise RuntimeError("blocked")
        return PAGE.replace("115/10/13", f"115/{m:02d}/13").replace("115/10/15", f"115/{m:02d}/15")
    doc = F.build_conferences(tmp_path, today=date(2026, 10, 13), fetch=fetch, sleep=0, stamp="s")
    assert [x["d"] for x in doc["items"]["1303"]] == ["2026-11-13", "2026-10-13", "2026-09-13", "2026-08-13"]
    assert len(doc["errors"]) == 4 and doc["errors"][0].startswith("otc")
    assert doc["slide"].startswith("https://mopsov.twse.com.tw/server-java/FileDownLoad?")
    assert json.loads((tmp_path / "conferences.json").read_text(encoding="utf-8"))["items"] == doc["items"]


def test_every_request_failing_raises_and_keeps_the_old_file(tmp_path):
    (tmp_path / "conferences.json").write_text('{"old": 1}', encoding="utf-8")
    def fetch(market, y, m):
        raise RuntimeError("blocked")
    with pytest.raises(RuntimeError):
        F.build_conferences(tmp_path, today=date(2026, 10, 13), fetch=fetch, sleep=0)
    assert json.loads((tmp_path / "conferences.json").read_text(encoding="utf-8")) == {"old": 1}
