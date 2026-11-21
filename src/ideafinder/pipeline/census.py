"""US business counts per industry, from the Census Bureau's County Business Patterns (CBP).

Used only for market math in Briefings (PLAN.md §1 Reference data). The Census API now needs a
key, but the same numbers are published as files that don't: one US-level file per year, plus a
file of industry names. `ideafinder census load` downloads both and stores one row per industry.

Industry codes are NAICS 2017 (what CBP still uses), padded in the files: "238///" is the 3-digit
code 238, "------" is the all-sectors total.
"""

import csv
import io
import zipfile
from dataclasses import dataclass

import httpx2
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import MarketStat

DEFAULT_YEAR = 2023
DATA_URL = "https://www2.census.gov/programs-surveys/cbp/datasets/{year}/cbp{yy}us.zip"
NAMES_URL = (
    "https://www2.census.gov/programs-surveys/cbp/technical-documentation/reference/"
    "naics-descriptions/naics2017.txt"
)
ALL_LEGAL_FORMS = "-"  # the file repeats each industry per legal form; "-" is the total


@dataclass(frozen=True)
class Row:
    naics_code: str
    label: str
    establishments: int


def normalise(code: str) -> str:
    return code.strip().rstrip("-/")


def parse(us_file: str, names_file: str) -> list[Row]:
    names = {
        normalise(r["NAICS"]): r["DESCRIPTION"].strip()
        for r in csv.DictReader(io.StringIO(names_file))
    }
    rows = []
    for r in csv.DictReader(io.StringIO(us_file)):
        code = normalise(r["naics"])
        if r["lfo"] != ALL_LEGAL_FORMS or not code:
            continue
        rows.append(Row(code, names.get(code, code), int(r["est"])))
    return rows


async def download(year: int = DEFAULT_YEAR) -> tuple[str, str]:
    async with httpx2.AsyncClient(timeout=120, follow_redirects=True) as client:
        data = await client.get(DATA_URL.format(year=year, yy=str(year)[2:]))
        data.raise_for_status()
        names = await client.get(NAMES_URL)
        names.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(data.content)) as z:
        [member] = [n for n in z.namelist() if n.endswith(".txt")]
        us_file = z.read(member).decode("utf-8")
    return us_file, names.content.decode("latin-1")


async def store(session: AsyncSession, rows: list[Row], year: int) -> int:
    for start in range(0, len(rows), 500):
        batch = rows[start : start + 500]
        stmt = insert(MarketStat).values(
            [
                {
                    "naics_code": r.naics_code,
                    "year": year,
                    "label": r.label,
                    "establishments": r.establishments,
                }
                for r in batch
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["naics_code", "year"],
                set_={"label": stmt.excluded.label, "establishments": stmt.excluded.establishments},
            )
        )
    await session.commit()
    return len(rows)
