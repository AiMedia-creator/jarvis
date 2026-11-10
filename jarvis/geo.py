"""Ortszuordnung fuer Meldungen: nur bei eindeutig genanntem Ort aus diesem Verzeichnis (reale Koordinaten).

Laender werden auf ihre Hauptstadt abgebildet (so gekennzeichnet). Ohne Treffer bleibt eine Meldung ohne Ort –
es werden keine Koordinaten geraten.
"""

from __future__ import annotations

import re

# name -> (Anzeige, Breite, Laenge). Staedte vor Laendern, damit der genauere Ort gewinnt.
CITIES = {
    "Berlin": ("Berlin", 52.520, 13.405), "Hamburg": ("Hamburg", 53.551, 9.993), "München": ("München", 48.137, 11.575),
    "Köln": ("Köln", 50.938, 6.960), "Frankfurt": ("Frankfurt am Main", 50.110, 8.682),
    "Stuttgart": ("Stuttgart", 48.776, 9.183), "Düsseldorf": ("Düsseldorf", 51.227, 6.773),
    "Leipzig": ("Leipzig", 51.340, 12.375), "Dresden": ("Dresden", 51.050, 13.738), "Hannover": ("Hannover", 52.376, 9.732),
    "Paris": ("Paris", 48.857, 2.352), "London": ("London", 51.507, -0.128), "Brüssel": ("Brüssel", 50.850, 4.352),
    "Wien": ("Wien", 48.208, 16.373), "Zürich": ("Zürich", 47.377, 8.541), "Rom": ("Rom", 41.903, 12.496),
    "Madrid": ("Madrid", 40.417, -3.704), "Warschau": ("Warschau", 52.230, 21.012), "Prag": ("Prag", 50.076, 14.438),
    "Amsterdam": ("Amsterdam", 52.368, 4.904), "Moskau": ("Moskau", 55.756, 37.617), "Kiew": ("Kyjiw", 50.450, 30.523),
    "Kyjiw": ("Kyjiw", 50.450, 30.523), "Istanbul": ("Istanbul", 41.008, 28.978), "Ankara": ("Ankara", 39.934, 32.860),
    "Jerusalem": ("Jerusalem", 31.769, 35.216), "Tel Aviv": ("Tel Aviv", 32.085, 34.782), "Gaza": ("Gaza", 31.502, 34.467),
    "Beirut": ("Beirut", 33.894, 35.502), "Teheran": ("Teheran", 35.689, 51.389), "Kairo": ("Kairo", 30.044, 31.236),
    "Washington": ("Washington, D.C.", 38.907, -77.037), "New York": ("New York", 40.713, -74.006),
    "Los Angeles": ("Los Angeles", 34.052, -118.244), "Peking": ("Peking", 39.904, 116.407),
    "Shanghai": ("Shanghai", 31.230, 121.474), "Tokio": ("Tokio", 35.676, 139.650), "Seoul": ("Seoul", 37.567, 126.978),
    "Neu-Delhi": ("Neu-Delhi", 28.614, 77.209), "Brasília": ("Brasília", -15.794, -47.882),
    "Taipeh": ("Taipeh", 25.033, 121.565), "Sydney": ("Sydney", -33.869, 151.209),
}
COUNTRIES = {  # Land -> Hauptstadt
    "Deutschland": ("Deutschland (Berlin)", 52.520, 13.405), "Frankreich": ("Frankreich (Paris)", 48.857, 2.352),
    "Großbritannien": ("Großbritannien (London)", 51.507, -0.128), "Italien": ("Italien (Rom)", 41.903, 12.496),
    "Spanien": ("Spanien (Madrid)", 40.417, -3.704), "Polen": ("Polen (Warschau)", 52.230, 21.012),
    "Österreich": ("Österreich (Wien)", 48.208, 16.373), "Schweiz": ("Schweiz (Bern)", 46.948, 7.447),
    "Russland": ("Russland (Moskau)", 55.756, 37.617), "Ukraine": ("Ukraine (Kyjiw)", 50.450, 30.523),
    "Türkei": ("Türkei (Ankara)", 39.934, 32.860), "Israel": ("Israel (Jerusalem)", 31.769, 35.216),
    "Iran": ("Iran (Teheran)", 35.689, 51.389), "Ägypten": ("Ägypten (Kairo)", 30.044, 31.236),
    "USA": ("USA (Washington, D.C.)", 38.907, -77.037), "China": ("China (Peking)", 39.904, 116.407),
    "Japan": ("Japan (Tokio)", 35.676, 139.650), "Indien": ("Indien (Neu-Delhi)", 28.614, 77.209),
    "Brasilien": ("Brasilien (Brasília)", -15.794, -47.882), "Taiwan": ("Taiwan (Taipeh)", 25.033, 121.565),
    "Südkorea": ("Südkorea (Seoul)", 37.567, 126.978), "Nordkorea": ("Nordkorea (Pjöngjang)", 39.039, 125.762),
    "Kanada": ("Kanada (Ottawa)", 45.421, -75.697), "Mexiko": ("Mexiko (Mexiko-Stadt)", 19.433, -99.133),
    "Australien": ("Australien (Canberra)", -35.281, 149.130), "Syrien": ("Syrien (Damaskus)", 33.514, 36.277),
    "Libanon": ("Libanon (Beirut)", 33.894, 35.502),
}


def _pattern(names) -> re.Pattern:
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\wÄÖÜäöüß-])({alternatives})(?![\wÄÖÜäöüß])")


_CITY = _pattern(CITIES)
_COUNTRY = _pattern(COUNTRIES)


def locate(text: str) -> dict | None:
    """Erster eindeutig genannter Ort (Stadt vor Land) oder None."""
    for pattern, table, kind in ((_CITY, CITIES, "stadt"), (_COUNTRY, COUNTRIES, "land")):
        match = pattern.search(text or "")
        if match:
            name, lat, lon = table[match.group(1)]
            return {"name": name, "lat": lat, "lon": lon, "art": kind}
    return None
