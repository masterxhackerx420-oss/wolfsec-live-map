"""One-time helper: build the small lookup tables used to place NWS alerts.

Most NWS alerts have no polygon, only county (SAME/FIPS) or zone (UGC) codes,
so we pre-compute one point per code:

  docs/data/us_counties.json  SAME code ("0"+state+county FIPS) -> [lat, lon]
      from the US Census 2024 Gazetteer (public domain)
  docs/data/us_zones.json     UGC zone code (e.g. "ORZ021", "PKZ130") -> [lat, lon]
      from NWS public / coastal-marine / offshore zone shapefiles (.dbf LAT/LON)

Re-run only if NWS republishes its zone files (update the ZONE_FILES names).
"""
import csv, io, json, pathlib, struct, zipfile, requests

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data"
GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip"
ZONE_FILES = ["z_16ap26", "mz16ap26", "oz16ap26"]  # see https://www.weather.gov/gis/AWIPSShapefiles


def read_dbf(raw: bytes):
    n, hlen, rlen = struct.unpack("<xxxxIHH", raw[:12])
    fields, pos = [], 32
    while raw[pos] != 0x0D:
        name = raw[pos:pos + 11].split(b"\0")[0].decode()
        fields.append((name, raw[pos + 16]))
        pos += 32
    for i in range(n):
        rec = raw[hlen + i * rlen: hlen + (i + 1) * rlen]
        if rec[:1] == b"*":
            continue
        off, row = 1, {}
        for name, size in fields:
            row[name] = rec[off:off + size].decode("latin-1").strip()
            off += size
        yield row


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(requests.get(GAZ, timeout=60).content)) as z:
        text = z.read(z.namelist()[0]).decode("latin-1")
    counties = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        row = {k.strip(): v.strip() for k, v in row.items()}
        counties["0" + row["GEOID"]] = [round(float(row["INTPTLAT"]), 4), round(float(row["INTPTLONG"]), 4)]
    (OUT / "us_counties.json").write_text(json.dumps(counties, separators=(",", ":")))
    print("counties:", len(counties))

    zones = {}
    for name in ZONE_FILES:
        url = f"https://www.weather.gov/source/gis/Shapefiles/WSOM/{name}.zip"
        with zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=120).content)) as z:
            dbf = z.read(next(n for n in z.namelist() if n.lower().endswith(".dbf")))
        for r in read_dbf(dbf):
            code = r.get("ID") or (r.get("STATE", "") + "Z" + r.get("ZONE", ""))
            try:
                zones[code] = [round(float(r["LAT"]), 4), round(float(r["LON"]), 4)]
            except (KeyError, ValueError):
                pass
    (OUT / "us_zones.json").write_text(json.dumps(zones, separators=(",", ":")))
    print("zones:", len(zones))


if __name__ == "__main__":
    main()
