"""Prayer (namaz) times for Faisalabad, computed astronomically for any date.

There is no hardcoded yearly table — every day is derived from the sun's
position, so the timetable covers 2026 the same as 2030. Conventions follow
the Pakistani standard (University of Islamic Sciences, Karachi): Fajr and
Isha at 18 degrees, Hanafi Asr (shadow twice the object), plus a couple of
minutes of precaution on Zuhr and Maghrib.

These are awwal (start) times. Zuhr in Faisalabad is a fixed 13:30 jamaat
year-round — the db layer keeps that as an editable fixed setting rather than
deriving it from the sun; the same layer carries jamaat offsets and per-date
overrides for the rest.
"""
import math
from datetime import date

# Faisalabad, Punjab, Pakistan. Pakistan is UTC+5 year-round (no DST).
LATITUDE = 31.4187
LONGITUDE = 73.0791
TIMEZONE = 5

FAJR_ANGLE = 18.0          # degrees below the horizon at dawn
ISHA_ANGLE = 18.0          # degrees below the horizon at dusk
SUNSET_ANGLE = 0.833       # refraction + solar disc radius
ASR_FACTOR = 2.0           # Hanafi: shadow = twice the object's length

# Small fixed precautions (minutes) on top of each computed instant, tuned to
# sit within a minute or two of the printed Faisalabad timetables.
PRELUDE_MIN = {"fajr": 0, "zuhr": 2, "asr": 5, "maghrib": 5, "isha": 3}

PRAYERS = ("fajr", "zuhr", "asr", "maghrib", "isha")


def _sun_position(jd: float) -> tuple[float, float]:
    """Solar declination (degrees) and equation of time (hours) for a Julian day."""
    d = jd - 2451545.0
    g = (357.529 + 0.98560028 * d) % 360.0
    q = (280.459 + 0.98564736 * d) % 360.0
    l = (q + 1.915 * math.sin(math.radians(g))
         + 0.020 * math.sin(math.radians(2 * g))) % 360.0
    e = 23.439 - 0.00000036 * d
    ra = math.degrees(math.atan2(
        math.cos(math.radians(e)) * math.sin(math.radians(l)),
        math.cos(math.radians(l)))) / 15.0
    decl = math.degrees(math.asin(
        math.sin(math.radians(e)) * math.sin(math.radians(l))))
    eqt = q / 15.0 - ra
    eqt = (eqt + 12.0) % 24.0 - 12.0
    return decl, eqt


def _hour_angle(decl: float, angle: float) -> float:
    """Hours from solar noon until the sun sits `angle` degrees below the
    horizon (a negative angle means above it, as for Asr)."""
    lat = math.radians(LATITUDE)
    d = math.radians(decl)
    a = math.radians(angle)
    x = (-math.sin(a) - math.sin(d) * math.sin(lat)) / (math.cos(d) * math.cos(lat))
    x = max(-1.0, min(1.0, x))
    return math.degrees(math.acos(x)) / 15.0


def day_times(on_date) -> dict[str, int]:
    """The five prayer start times for one date, minutes-of-day, local."""
    if isinstance(on_date, str):
        on_date = date.fromisoformat(on_date)
    jd = on_date.toordinal() + 1721425.0  # Julian day at noon UT
    decl, eqt = _sun_position(jd)
    noon = (12.0 + TIMEZONE - LONGITUDE / 15.0 - eqt) * 60.0  # minutes of day
    asr_alt = math.degrees(math.atan(1.0 / (ASR_FACTOR + math.tan(
        math.radians(abs(LATITUDE - decl))))))
    def at(angle: float) -> float:
        """Minutes-of-day when the sun sits `angle` degrees below the horizon."""
        return noon + _hour_angle(decl, angle) * 60.0
    return {
        "fajr": round(noon - _hour_angle(decl, FAJR_ANGLE) * 60.0) % 1440,
        "zuhr": round(noon) + PRELUDE_MIN["zuhr"],  # awwal midday; jamaat is fixed
        "asr": round(at(-asr_alt) + PRELUDE_MIN["asr"]) % 1440,
        "maghrib": round(at(SUNSET_ANGLE) + PRELUDE_MIN["maghrib"]) % 1440,
        "isha": round(at(ISHA_ANGLE)) % 1440,
    }
