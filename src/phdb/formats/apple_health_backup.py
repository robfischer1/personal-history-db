"""Apple Health backup SQLite parser — yields typed parsed records.

Source: healthdb_secure.sqlite + healthdb.sqlite extracted from an encrypted
iOS backup (iMazing, iTunes, etc.) via iphone_backup_decrypt.

Yields the same ParsedRecord / ParsedWorkout dataclasses as apple_health_xml.py
so the adapter layer can consume either source identically.

No clinical records — those are only in the XML export.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Union

from phdb.formats.apple_health_xml import (
    HRSample,
    MetadataEntry,
    ParsedRecord,
    ParsedWorkout,
    WorkoutEvent,
    WorkoutStatistic,
)

MAX_BODY_LEN = 2000

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)

# ---------------------------------------------------------------------------
# data_type integer → HealthKit type string mapping
# Source: github.com/christophhagen/HealthDB (iOS SDK enum values)
# ---------------------------------------------------------------------------

_QUANTITY_TYPES: dict[int, str] = {
    0: "HKQuantityTypeIdentifierBodyMassIndex",
    1: "HKQuantityTypeIdentifierBodyFatPercentage",
    2: "HKQuantityTypeIdentifierHeight",
    3: "HKQuantityTypeIdentifierBodyMass",
    4: "HKQuantityTypeIdentifierLeanBodyMass",
    5: "HKQuantityTypeIdentifierHeartRate",
    7: "HKQuantityTypeIdentifierStepCount",
    8: "HKQuantityTypeIdentifierDistanceWalkingRunning",
    9: "HKQuantityTypeIdentifierBasalEnergyBurned",
    10: "HKQuantityTypeIdentifierActiveEnergyBurned",
    12: "HKQuantityTypeIdentifierFlightsClimbed",
    14: "HKQuantityTypeIdentifierOxygenSaturation",
    15: "HKQuantityTypeIdentifierBloodGlucose",
    16: "HKQuantityTypeIdentifierBloodPressureSystolic",
    17: "HKQuantityTypeIdentifierBloodPressureDiastolic",
    18: "HKQuantityTypeIdentifierBloodAlcoholContent",
    19: "HKQuantityTypeIdentifierPeripheralPerfusionIndex",
    20: "HKQuantityTypeIdentifierDietaryFatTotal",
    21: "HKQuantityTypeIdentifierDietaryFatPolyunsaturated",
    22: "HKQuantityTypeIdentifierDietaryFatMonounsaturated",
    23: "HKQuantityTypeIdentifierDietaryFatSaturated",
    24: "HKQuantityTypeIdentifierDietaryCholesterol",
    25: "HKQuantityTypeIdentifierDietarySodium",
    26: "HKQuantityTypeIdentifierDietaryCarbohydrates",
    27: "HKQuantityTypeIdentifierDietaryFiber",
    28: "HKQuantityTypeIdentifierDietarySugar",
    29: "HKQuantityTypeIdentifierDietaryEnergyConsumed",
    30: "HKQuantityTypeIdentifierDietaryProtein",
    31: "HKQuantityTypeIdentifierDietaryVitaminA",
    32: "HKQuantityTypeIdentifierDietaryVitaminB6",
    33: "HKQuantityTypeIdentifierDietaryVitaminB12",
    34: "HKQuantityTypeIdentifierDietaryVitaminC",
    35: "HKQuantityTypeIdentifierDietaryVitaminD",
    36: "HKQuantityTypeIdentifierDietaryVitaminE",
    37: "HKQuantityTypeIdentifierDietaryVitaminK",
    38: "HKQuantityTypeIdentifierDietaryCalcium",
    39: "HKQuantityTypeIdentifierDietaryIron",
    40: "HKQuantityTypeIdentifierDietaryThiamin",
    41: "HKQuantityTypeIdentifierDietaryRiboflavin",
    42: "HKQuantityTypeIdentifierDietaryNiacin",
    43: "HKQuantityTypeIdentifierDietaryFolate",
    44: "HKQuantityTypeIdentifierDietaryBiotin",
    45: "HKQuantityTypeIdentifierDietaryPantothenicAcid",
    46: "HKQuantityTypeIdentifierDietaryPhosphorus",
    47: "HKQuantityTypeIdentifierDietaryIodine",
    48: "HKQuantityTypeIdentifierDietaryMagnesium",
    49: "HKQuantityTypeIdentifierDietaryZinc",
    50: "HKQuantityTypeIdentifierDietarySelenium",
    51: "HKQuantityTypeIdentifierDietaryCopper",
    52: "HKQuantityTypeIdentifierDietaryManganese",
    53: "HKQuantityTypeIdentifierDietaryChromium",
    54: "HKQuantityTypeIdentifierDietaryMolybdenum",
    55: "HKQuantityTypeIdentifierDietaryChloride",
    56: "HKQuantityTypeIdentifierDietaryPotassium",
    57: "HKQuantityTypeIdentifierNumberOfTimesFallen",
    58: "HKQuantityTypeIdentifierElectrodermalActivity",
    60: "HKQuantityTypeIdentifierInhalerUsage",
    61: "HKQuantityTypeIdentifierRespiratoryRate",
    62: "HKQuantityTypeIdentifierBodyTemperature",
    72: "HKQuantityTypeIdentifierForcedExpiratoryVolume1",
    73: "HKQuantityTypeIdentifierPeakExpiratoryFlowRate",
    75: "HKQuantityTypeIdentifierAppleExerciseTime",
    78: "HKQuantityTypeIdentifierDietaryCaffeine",
    83: "HKQuantityTypeIdentifierDistanceCycling",
    87: "HKQuantityTypeIdentifierDietaryWater",
    89: "HKQuantityTypeIdentifierUVExposure",
    90: "HKQuantityTypeIdentifierBasalBodyTemperature",
    101: "HKQuantityTypeIdentifierPushCount",
    110: "HKQuantityTypeIdentifierDistanceSwimming",
    111: "HKQuantityTypeIdentifierSwimmingStrokeCount",
    113: "HKQuantityTypeIdentifierDistanceWheelchair",
    114: "HKQuantityTypeIdentifierWaistCircumference",
    118: "HKQuantityTypeIdentifierRestingHeartRate",
    124: "HKQuantityTypeIdentifierVO2Max",
    125: "HKQuantityTypeIdentifierInsulinDelivery",
    137: "HKQuantityTypeIdentifierWalkingHeartRateAverage",
    138: "HKQuantityTypeIdentifierDistanceDownhillSnowSports",
    139: "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    172: "HKQuantityTypeIdentifierEnvironmentalAudioExposure",
    173: "HKQuantityTypeIdentifierHeadphoneAudioExposure",
    182: "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage",
    183: "HKQuantityTypeIdentifierSixMinuteWalkTestDistance",
    186: "HKQuantityTypeIdentifierAppleStandTime",
    187: "HKQuantityTypeIdentifierWalkingSpeed",
    188: "HKQuantityTypeIdentifierWalkingStepLength",
    194: "HKQuantityTypeIdentifierWalkingAsymmetryPercentage",
    195: "HKQuantityTypeIdentifierStairAscentSpeed",
    196: "HKQuantityTypeIdentifierStairDescentSpeed",
    248: "HKQuantityTypeIdentifierAtrialFibrillationBurden",
    249: "HKQuantityTypeIdentifierAppleWalkingSteadiness",
    251: "HKQuantityTypeIdentifierNumberOfAlcoholicBeverages",
    258: "HKQuantityTypeIdentifierRunningStrideLength",
    259: "HKQuantityTypeIdentifierRunningVerticalOscillation",
    260: "HKQuantityTypeIdentifierRunningGroundContactTime",
    266: "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute",
    269: "HKQuantityTypeIdentifierUnderwaterDepth",
    270: "HKQuantityTypeIdentifierRunningPower",
    272: "HKQuantityTypeIdentifierEnvironmentalSoundReduction",
    274: "HKQuantityTypeIdentifierRunningSpeed",
    277: "HKQuantityTypeIdentifierWaterTemperature",
    279: "HKQuantityTypeIdentifierTimeInDaylight",
    280: "HKQuantityTypeIdentifierCyclingPower",
    281: "HKQuantityTypeIdentifierCyclingSpeed",
    282: "HKQuantityTypeIdentifierCyclingCadence",
    283: "HKQuantityTypeIdentifierCyclingFunctionalThresholdPower",
    286: "HKQuantityTypeIdentifierPhysicalEffort",
}

_CATEGORY_TYPES: dict[int, str] = {
    63: "HKCategoryTypeIdentifierSleepAnalysis",
    70: "HKCategoryTypeIdentifierAppleStandHour",
    91: "HKCategoryTypeIdentifierCervicalMucusQuality",
    92: "HKCategoryTypeIdentifierOvulationTestResult",
    95: "HKCategoryTypeIdentifierMenstrualFlow",
    96: "HKCategoryTypeIdentifierIntermenstrualBleeding",
    97: "HKCategoryTypeIdentifierSexualActivity",
    99: "HKCategoryTypeIdentifierMindfulSession",
    147: "HKCategoryTypeIdentifierLowHeartRateEvent",
    178: "HKCategoryTypeIdentifierEnvironmentalAudioExposureEvent",
    189: "HKCategoryTypeIdentifierToothbrushingEvent",
    191: "HKCategoryTypeIdentifierPregnancy",
    192: "HKCategoryTypeIdentifierLactation",
    193: "HKCategoryTypeIdentifierContraceptive",
    237: "HKCategoryTypeIdentifierHandwashingEvent",
    262: "HKCategoryTypeIdentifierIrregularMenstrualCycles",
    263: "HKCategoryTypeIdentifierProlongedMenstrualPeriods",
    264: "HKCategoryTypeIdentifierPersistentIntermenstrualBleeding",
}

_OTHER_TYPES: dict[int, str] = {
    67: "WeeklyCalorieGoal",
    76: "WorkoutActivity",
    79: "Workout",
    102: "WorkoutRoute",
    104: "StandHourGoal",
    105: "ExerciseMinutesGoal",
    116: "AppleWatchIsCharging",
    119: "HeartbeatSeries",
    144: "ECGSample",
    145: "Audiogram",
    198: "SleepSchedule",
    287: "AnxietyRiskQuestionnaire",
    288: "DepressionRiskQuestionnaire",
}

_CORRELATION_TYPES: dict[int, str] = {
    80: "HKCorrelationTypeIdentifierBloodPressure",
    81: "HKCorrelationTypeIdentifierFood",
}

# Canonical units stored in healthdb_secure (Apple's internal unit system).
# quantity_samples.quantity is always in the canonical unit.
_CANONICAL_UNITS: dict[str, str] = {
    "HeartRate": "count/min",
    "StepCount": "count",
    "DistanceWalkingRunning": "mi",
    "BasalEnergyBurned": "Cal",
    "ActiveEnergyBurned": "Cal",
    "FlightsClimbed": "count",
    "OxygenSaturation": "%",
    "BodyMass": "lb",
    "Height": "in",
    "BodyMassIndex": "count",
    "BodyFatPercentage": "%",
    "RespiratoryRate": "count/min",
    "BodyTemperature": "degF",
    "BloodGlucose": "mg/dL",
    "BloodPressureSystolic": "mmHg",
    "BloodPressureDiastolic": "mmHg",
    "HeartRateVariabilitySDNN": "ms",
    "RestingHeartRate": "count/min",
    "WalkingHeartRateAverage": "count/min",
    "VO2Max": "mL/min·kg",
    "AppleExerciseTime": "min",
    "AppleStandTime": "min",
    "AppleStandHour": "",
    "WalkingDoubleSupportPercentage": "%",
    "WalkingAsymmetryPercentage": "%",
    "WalkingSpeed": "mi/hr",
    "WalkingStepLength": "in",
    "StairAscentSpeed": "ft/s",
    "StairDescentSpeed": "ft/s",
    "SixMinuteWalkTestDistance": "m",
    "EnvironmentalAudioExposure": "dBASPL",
    "HeadphoneAudioExposure": "dBASPL",
    "TimeInDaylight": "min",
    "PhysicalEffort": "kcal/hr·kg",
    "RunningPower": "W",
    "RunningSpeed": "mi/hr",
    "EnvironmentalSoundReduction": "dBASPL",
    "DietaryWater": "fl_oz_us",
    "DietaryEnergyConsumed": "Cal",
    "DietaryFatTotal": "g",
    "DietaryProtein": "g",
    "DietaryCarbohydrates": "g",
    "DietarySodium": "mg",
    "DietarySugar": "g",
    "DietaryFiber": "g",
    "DietaryCholesterol": "mg",
    "DietaryFatSaturated": "g",
    "DietaryFatMonounsaturated": "g",
    "DietaryFatPolyunsaturated": "g",
    "DietaryPotassium": "mg",
    "DietaryCaffeine": "mg",
    "SleepAnalysis": "",
    "MindfulSession": "",
}

# Conversion factors: canonical DB unit → display unit used by the XML export.
# Most quantity_samples.quantity values are stored in SI-ish canonical units
# (e.g. m for distance, count/s for heart rate). The XML export uses the
# user's preferred units. We convert to match XML adapter output.
_CANONICAL_CONVERSIONS: dict[str, tuple[float, str]] = {
    "HeartRate": (60.0, "count/min"),
    "DistanceWalkingRunning": (1.0 / 1609.344, "mi"),
    "WalkingSpeed": (2.23694, "mi/hr"),
    "WalkingStepLength": (39.3701, "in"),
    "RunningSpeed": (2.23694, "mi/hr"),
    "StairAscentSpeed": (3.28084, "ft/s"),
    "StairDescentSpeed": (3.28084, "ft/s"),
    "Height": (39.3701, "in"),
    "BodyMass": (2.20462, "lb"),
    "RespiratoryRate": (60.0, "count/min"),
    "RestingHeartRate": (60.0, "count/min"),
    "WalkingHeartRateAverage": (60.0, "count/min"),
    "HeartRateRecoveryOneMinute": (60.0, "count/min"),
    "AppleStandTime": (1.0 / 60.0, "min"),
    "BasalEnergyBurned": (1.0 / 4184.0, "Cal"),
    "ActiveEnergyBurned": (1.0 / 4184.0, "Cal"),
    "DietaryEnergyConsumed": (1.0 / 4184.0, "Cal"),
    "HeartRateVariabilitySDNN": (1000.0, "ms"),
}

# Workout activity_type integer → HKWorkoutActivityType string
_WORKOUT_ACTIVITY_TYPES: dict[int, str] = {
    1: "HKWorkoutActivityTypeAmericanFootball",
    2: "HKWorkoutActivityTypeArchery",
    3: "HKWorkoutActivityTypeAustralianFootball",
    4: "HKWorkoutActivityTypeBadminton",
    5: "HKWorkoutActivityTypeBaseball",
    6: "HKWorkoutActivityTypeBasketball",
    7: "HKWorkoutActivityTypeBowling",
    8: "HKWorkoutActivityTypeBoxing",
    9: "HKWorkoutActivityTypeClimbing",
    10: "HKWorkoutActivityTypeCricket",
    11: "HKWorkoutActivityTypeCrossTraining",
    13: "HKWorkoutActivityTypeCycling",
    16: "HKWorkoutActivityTypeElliptical",
    18: "HKWorkoutActivityTypeFencing",
    19: "HKWorkoutActivityTypeFishing",
    20: "HKWorkoutActivityTypeFunctionalStrengthTraining",
    21: "HKWorkoutActivityTypeGolf",
    24: "HKWorkoutActivityTypeHiking",
    25: "HKWorkoutActivityTypeHockey",
    26: "HKWorkoutActivityTypeHunting",
    28: "HKWorkoutActivityTypeMartialArts",
    29: "HKWorkoutActivityTypeMindAndBody",
    35: "HKWorkoutActivityTypePreparationAndRecovery",
    37: "HKWorkoutActivityTypeRowing",
    39: "HKWorkoutActivityTypeRunning",
    41: "HKWorkoutActivityTypeSailing",
    46: "HKWorkoutActivityTypeSoccer",
    47: "HKWorkoutActivityTypeSoftball",
    50: "HKWorkoutActivityTypeStairClimbing",
    52: "HKWorkoutActivityTypeSwimming",
    56: "HKWorkoutActivityTypeTrackAndField",
    50: "HKWorkoutActivityTypeStairClimbing",
    57: "HKWorkoutActivityTypeTraditionalStrengthTraining",
    58: "HKWorkoutActivityTypeVolleyball",
    59: "HKWorkoutActivityTypeWalking",
    60: "HKWorkoutActivityTypeWaterFitness",
    62: "HKWorkoutActivityTypeWrestling",
    63: "HKWorkoutActivityTypeYoga",
    73: "HKWorkoutActivityTypeOther",
    74: "HKWorkoutActivityTypeCoreTraining",
    75: "HKWorkoutActivityTypeCrossCountrySkiing",
    76: "HKWorkoutActivityTypeDownhillSkiing",
    77: "HKWorkoutActivityTypeFlexibility",
    78: "HKWorkoutActivityTypeHighIntensityIntervalTraining",
    79: "HKWorkoutActivityTypeJumpRope",
    82: "HKWorkoutActivityTypePilates",
    84: "HKWorkoutActivityTypeStairs",
    85: "HKWorkoutActivityTypeStepTraining",
    87: "HKWorkoutActivityTypeFitnessGaming",
}


def _apple_ts_to_iso(ts: float | None) -> str | None:
    """Convert Apple epoch timestamp (seconds since 2001-01-01) to ISO-8601 UTC."""
    if ts is None:
        return None
    dt = APPLE_EPOCH + timedelta(seconds=ts)
    return dt.isoformat()


def _strip_hk_prefix(s: str) -> str:
    for p in ("HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier",
              "HKWorkoutActivityType", "HKCorrelationTypeIdentifier",
              "HKDataType"):
        if s.startswith(p):
            return s[len(p):]
    return s


def _resolve_type(data_type: int) -> tuple[str, str]:
    """Return (full_type_string, label) for a data_type integer."""
    for mapping in (_QUANTITY_TYPES, _CATEGORY_TYPES, _CORRELATION_TYPES):
        if data_type in mapping:
            full = mapping[data_type]
            return full, _strip_hk_prefix(full)
    if data_type in _OTHER_TYPES:
        name = _OTHER_TYPES[data_type]
        return name, name
    return f"UnknownType{data_type}", f"UnknownType{data_type}"


def _format_value(label: str, raw_quantity: float | None) -> tuple[str, str]:
    """Convert canonical quantity to display value + unit string."""
    if raw_quantity is None:
        return "", ""
    if label in _CANONICAL_CONVERSIONS:
        factor, unit = _CANONICAL_CONVERSIONS[label]
        display = raw_quantity * factor
    else:
        unit = _CANONICAL_UNITS.get(label, "")
        display = raw_quantity

    if display == int(display):
        return str(int(display)), unit
    return f"{display:.3g}", unit


def parse(
    secure_db_path: Path,
    meta_db_path: Path | None = None,
    *,
    since_ts: float | None = None,
) -> Iterator[Union[ParsedRecord, ParsedWorkout]]:
    """Yield ParsedRecord / ParsedWorkout from backup Health SQLite databases.

    Args:
        secure_db_path: Path to healthdb_secure.sqlite
        meta_db_path: Path to healthdb.sqlite (for sources table). Optional.
        since_ts: Apple epoch timestamp; only yield samples created after this.
    """
    con = sqlite3.connect(f"file:{secure_db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    sources: dict[int, str] = {}
    if meta_db_path and meta_db_path.exists():
        meta_con = sqlite3.connect(f"file:{meta_db_path}?mode=ro", uri=True)
        for row in meta_con.execute("SELECT ROWID, name FROM sources"):
            sources[row[0]] = row[1]
        meta_con.close()

    unit_strings: dict[int, str] = {}
    for row in con.execute("SELECT ROWID, unit_string FROM unit_strings"):
        unit_strings[row[0]] = row[1]

    yield from _iter_records(con, sources, unit_strings, since_ts)
    yield from _iter_workouts(con, sources, since_ts)

    con.close()


def _iter_records(
    con: sqlite3.Connection,
    sources: dict[int, str],
    unit_strings: dict[int, str],
    since_ts: float | None,
) -> Iterator[ParsedRecord]:
    """Yield ParsedRecord for quantity + category samples."""
    where = "AND o.creation_date > ?" if since_ts else ""
    params: tuple = (since_ts,) if since_ts else ()

    query = f"""
        SELECT s.data_id, s.start_date, s.end_date, s.data_type,
               o.creation_date, o.provenance,
               q.quantity, q.original_quantity, q.original_unit,
               c.value as cat_value
        FROM samples s
        JOIN objects o ON o.data_id = s.data_id
        LEFT JOIN quantity_samples q ON q.data_id = s.data_id
        LEFT JOIN category_samples c ON c.data_id = s.data_id
        WHERE s.data_type NOT IN (67, 76, 79, 102, 104, 105, 116, 119, 144, 145, 198, 287, 288)
        {where}
        ORDER BY s.data_id
    """
    for row in con.execute(query, params):
        data_type = row["data_type"]
        full_type, label = _resolve_type(data_type)

        if data_type in _QUANTITY_TYPES:
            raw_q = row["quantity"]
            value_str, unit = _format_value(label, raw_q)
        elif data_type in _CATEGORY_TYPES:
            cat_val = row["cat_value"]
            value_str = str(cat_val) if cat_val is not None else ""
            unit = ""
        else:
            value_str = ""
            unit = ""

        start_date = _apple_ts_to_iso(row["start_date"])
        end_date = _apple_ts_to_iso(row["end_date"])

        prov_id = row["provenance"]
        source_name = sources.get(prov_id, "") if prov_id else ""

        subject = (
            f"{label}: {value_str}{(' ' + unit) if unit else ''}"
            if value_str
            else label
        )
        body_text = subject[:MAX_BODY_LEN]

        dedup_seed = (
            f"apple-health|record|{full_type}|{start_date}|{end_date}"
            f"|{value_str}|{unit}|{source_name}"
        )
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        yield ParsedRecord(
            record_type=full_type,
            record_type_label=label,
            unit=unit,
            value=value_str,
            source_name=source_name,
            start_date=start_date,
            end_date=end_date,
            subject=subject,
            body_text=body_text,
            raw_hash=raw_hash,
        )


def _iter_workouts(
    con: sqlite3.Connection,
    sources: dict[int, str],
    since_ts: float | None,
) -> Iterator[ParsedWorkout]:
    """Yield ParsedWorkout for workout samples."""
    where = "AND o.creation_date > ?" if since_ts else ""
    params: tuple = (since_ts,) if since_ts else ()

    query = f"""
        SELECT s.data_id, s.start_date, s.end_date,
               o.creation_date, o.provenance,
               wa.activity_type, wa.duration,
               w.total_distance, w.goal_type, w.goal
        FROM samples s
        JOIN objects o ON o.data_id = s.data_id
        JOIN workouts w ON w.data_id = s.data_id
        LEFT JOIN workout_activities wa ON wa.owner_id = s.data_id AND wa.is_primary_activity = 1
        WHERE s.data_type = 79
        {where}
        ORDER BY s.data_id
    """
    for row in con.execute(query, params):
        data_id = row["data_id"]
        start_date = _apple_ts_to_iso(row["start_date"])
        end_date = _apple_ts_to_iso(row["end_date"])
        prov_id = row["provenance"]
        source_name = sources.get(prov_id, "") if prov_id else ""

        activity_type_int = row["activity_type"] or 0
        activity_full = _WORKOUT_ACTIVITY_TYPES.get(
            activity_type_int, f"HKWorkoutActivityType{activity_type_int}"
        )
        activity_label = _strip_hk_prefix(activity_full)

        duration = row["duration"]
        duration_str = f"{duration:.2f}" if duration else None
        total_distance = row["total_distance"]
        distance_str = f"{total_distance:.3f}" if total_distance else None

        parts = [f"Workout: {activity_label}"]
        if duration_str:
            parts.append(f"duration {duration_str} min")
        if distance_str:
            parts.append(f"distance {distance_str} mi")
        body_text = " | ".join(parts)[:MAX_BODY_LEN]
        subject = parts[0]

        dedup_seed = (
            f"apple-health|workout|{activity_full}|{start_date}|{end_date}|{source_name}"
        )
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        events: list[WorkoutEvent] = []
        for ev in con.execute(
            "SELECT type, date, duration FROM workout_events WHERE owner_id = ?",
            (data_id,),
        ):
            events.append(WorkoutEvent(
                event_type=str(ev["type"]) if ev["type"] is not None else None,
                date=_apple_ts_to_iso(ev["date"]),
                duration_seconds=ev["duration"],
            ))

        statistics: list[WorkoutStatistic] = []

        yield ParsedWorkout(
            activity_type=activity_full,
            activity_label=activity_label,
            duration=duration_str,
            duration_unit="min",
            total_distance=distance_str,
            distance_unit="mi",
            energy_burned=None,
            energy_unit="",
            source_name=source_name,
            start_date=start_date,
            end_date=end_date,
            subject=subject,
            body_text=body_text,
            raw_hash=raw_hash,
            events=tuple(events),
            statistics=tuple(statistics),
            gpx_points=(),
        )
