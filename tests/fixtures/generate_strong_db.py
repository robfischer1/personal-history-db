"""Generate synthetic Strong4.sqlite fixture for testing the Strong adapter."""

import os
import sqlite3

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "strong")
DB_PATH = os.path.join(FIXTURE_DIR, "Strong4.sqlite")


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)

    # Remove existing DB if present
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Create Core Data tables
    cur.execute("""
        CREATE TABLE ZSWORKOUT (
            Z_PK INTEGER PRIMARY KEY,
            ZSTARTDATE REAL,
            ZDURATION REAL,
            ZNAME TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE ZSSETGROUP (
            Z_PK INTEGER PRIMARY KEY,
            ZWORKOUT INTEGER,
            ZEXERCISE INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE ZSEXERCISE (
            Z_PK INTEGER PRIMARY KEY,
            ZNAME TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE ZSEXERCISESET (
            Z_PK INTEGER PRIMARY KEY,
            ZSETGROUP INTEGER,
            ZREPETITIONS INTEGER,
            ZWEIGHT REAL,
            ZSECONDS REAL
        )
    """)

    # Workout 1: Morning Workout
    # ZSTARTDATE=700000000 (~2023-03-10 in Apple epoch, offset 978307200)
    cur.execute(
        "INSERT INTO ZSWORKOUT VALUES (1, 700000000, 3600, 'Morning Workout')"
    )

    # Workout 2: Evening Workout
    cur.execute(
        "INSERT INTO ZSWORKOUT VALUES (2, 700100000, 1800, 'Evening Workout')"
    )

    # Exercises
    cur.execute("INSERT INTO ZSEXERCISE VALUES (1, 'Bench Press')")
    cur.execute("INSERT INTO ZSEXERCISE VALUES (2, 'Squat')")
    cur.execute("INSERT INTO ZSEXERCISE VALUES (3, 'Plank')")

    # SetGroups
    cur.execute("INSERT INTO ZSSETGROUP VALUES (1, 1, 1)")  # Workout 1, Bench Press
    cur.execute("INSERT INTO ZSSETGROUP VALUES (2, 1, 2)")  # Workout 1, Squat
    cur.execute("INSERT INTO ZSSETGROUP VALUES (3, 2, 3)")  # Workout 2, Plank

    # ExerciseSets
    cur.execute(
        "INSERT INTO ZSEXERCISESET VALUES (1, 1, 10, 60.0, NULL)"
    )  # Bench Press set 1
    cur.execute(
        "INSERT INTO ZSEXERCISESET VALUES (2, 1, 8, 70.0, NULL)"
    )  # Bench Press set 2
    cur.execute(
        "INSERT INTO ZSEXERCISESET VALUES (3, 2, 12, 80.0, NULL)"
    )  # Squat set 1
    cur.execute(
        "INSERT INTO ZSEXERCISESET VALUES (4, 3, NULL, NULL, 60.0)"
    )  # Plank set 1

    conn.commit()
    conn.close()
    print(f"Created {DB_PATH}")


if __name__ == "__main__":
    main()
