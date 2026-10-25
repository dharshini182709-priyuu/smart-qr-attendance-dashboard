
import os
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from flask import Flask, render_template, request, Response
from sqlalchemy import create_engine, text

app = Flask(__name__)


# Read the PostgreSQL connection URL from the environment
def get_database_url():
    url = os.environ.get("DATABASE_URL", "").strip()

    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Set DATABASE_URL in Windows "
            "User Environment Variables or Render Environment settings."
        )

    parts = urlsplit(url)

    if parts.scheme == "postgres":
        url = urlunsplit(
            ("postgresql", parts.netloc, parts.path,
             parts.query, parts.fragment)
        )

    if url.startswith("postgresql://"):
        url = url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    elif url.startswith("postgres://"):
        url = url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )

    return url


engine = create_engine(
    get_database_url(),
    pool_pre_ping=True
)


# Fetch records without modifying the database
def get_data():
    with engine.connect() as connection:
        students = pd.read_sql_query(
            text("""
                SELECT student_id, name
                FROM public.students
                ORDER BY name
            """),
            connection
        )

        attendance = pd.read_sql_query(
            text("""
                SELECT id, student_id, date, time, device_id
                FROM public.attendance
            """),
            connection
        )

    students["student_id"] = (
        students["student_id"].fillna("").astype(str).str.strip()
    )
    students["name"] = students["name"].fillna("").astype(str)

    if not attendance.empty:
        attendance["student_id"] = (
            attendance["student_id"].fillna("").astype(str).str.strip()
        )
        attendance["date"] = pd.to_datetime(
            attendance["date"], errors="coerce"
        )
        attendance = attendance.dropna(subset=["date"]).copy()

    return students, attendance


# Prepare chart data with readable labels
def make_chart_data(attendance, date_format):
    if attendance.empty:
        return []

    grouped = (
        attendance.assign(
            label=attendance["date"].dt.strftime(date_format)
        )
        .groupby("label")
        .size()
        .reset_index(name="count")
    )

    return [
        {
            "label": str(row["label"]),
            "count": int(row["count"])
        }
        for _, row in grouped.iterrows()
    ]


@app.route("/")
def dashboard():
    students, attendance = get_data()

    selected_date = request.args.get("date", "").strip()
    selected_month = request.args.get("month", "").strip()
    search = request.args.get("search", "").strip()

    filtered_attendance = attendance.copy()

    # Filter by date
    if selected_date and not filtered_attendance.empty:
        filtered_attendance = filtered_attendance[
            filtered_attendance["date"].dt.strftime("%Y-%m-%d")
            == selected_date
        ]

    # Filter by month
    if selected_month and not filtered_attendance.empty:
        filtered_attendance = filtered_attendance[
            filtered_attendance["date"].dt.strftime("%Y-%m")
            == selected_month
        ]

    total_students = int(len(students))
    total_records = int(len(filtered_attendance))

    total_days = (
        int(filtered_attendance["date"].dt.date.nunique())
        if not filtered_attendance.empty
        else 0
    )

    # Count attendance records for each student
    if filtered_attendance.empty:
        counts = pd.DataFrame(
            columns=["student_id", "count"]
        )
    else:
        counts = (
            filtered_attendance.groupby("student_id")
            .size()
            .reset_index(name="count")
        )

    # Keep every student, including students with zero attendance
    student_data = students.merge(
        counts,
        on="student_id",
        how="left"
    )

    student_data["count"] = (
        student_data["count"].fillna(0).astype(int)
    )

    # Calculate attendance percentage
    if total_days > 0:
        student_data["percentage"] = (
            student_data["count"] / total_days * 100
        ).clip(upper=100)
    else:
        student_data["percentage"] = 0.0

    student_data["percentage"] = (
        student_data["percentage"].round(1)
    )

    # Search by student name or register number
    if search:
        search_text = search.casefold()

        matches = (
            student_data["name"].str.casefold().str.contains(
                search_text, na=False, regex=False
            )
            |
            student_data["student_id"].str.casefold().str.contains(
                search_text, na=False, regex=False
            )
        )

        student_data = student_data[matches].copy()

    below_75 = int(
        (student_data["percentage"] < 75).sum()
    )

    average_attendance = (
        round(float(student_data["percentage"].mean()), 1)
        if not student_data.empty
        else 0.0
    )

    # Daily and monthly chart labels
    daily_data = make_chart_data(
        filtered_attendance, "%d %b %Y"
    )
    monthly_data = make_chart_data(
        filtered_attendance, "%b %Y"
    )

    # Sort chart dates in chronological order
    daily_data.sort(
        key=lambda item: pd.to_datetime(
            item["label"], format="%d %b %Y"
        )
    )

    monthly_data.sort(
        key=lambda item: pd.to_datetime(
            item["label"], format="%b %Y"
        )
    )

    # Provide all matching students to the HTML chart
    chart_students = student_data.sort_values(
        by=["percentage", "name"],
        ascending=[False, True]
    )

    # Ensure all required fields exist for dashboard.html
    student_records = (
        student_data.sort_values("name")
        .to_dict(orient="records")
    )

    chart_records = chart_students.to_dict(
        orient="records"
    )

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_records=total_records,
        total_days=total_days,
        average_attendance=average_attendance,
        below_75=below_75,
        students=student_records,
        daily_data=daily_data,
        monthly_data=monthly_data,
        student_data=chart_records,
        selected_date=selected_date,
        selected_month=selected_month,
        search=search
    )


@app.route("/download")
def download():
    students, attendance = get_data()

    total_days = (
        int(attendance["date"].dt.date.nunique())
        if not attendance.empty
        else 0
    )

    if attendance.empty:
        counts = pd.DataFrame(
            columns=["student_id", "attendance_count"]
        )
    else:
        counts = (
            attendance.groupby("student_id")
            .size()
            .reset_index(name="attendance_count")
        )

    report = students.merge(
        counts,
        on="student_id",
        how="left"
    )

    report["attendance_count"] = (
        report["attendance_count"].fillna(0).astype(int)
    )

    if total_days > 0:
        report["attendance_percentage"] = (
            report["attendance_count"] / total_days * 100
        ).clip(upper=100)
    else:
        report["attendance_percentage"] = 0.0

    report["attendance_percentage"] = (
        report["attendance_percentage"].round(1)
    )

    csv_data = report.to_csv(index=False)

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            'attachment; filename="attendance_report.csv"'
        }
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=False
    )
