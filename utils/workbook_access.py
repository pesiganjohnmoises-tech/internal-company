"""Country-scoped workbook submission permission; independent of directory grants."""
SCHEMA="""
CREATE TABLE IF NOT EXISTS user_workbook_upload_access(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    country_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
    PRIMARY KEY(user_id,country_id)
);
"""

def submission_countries(con,user_id):
    """Read current eligibility, so revoked grants/roles never survive in a cookie."""
    return con.execute("""SELECT cn.id,cn.name FROM countries cn
        JOIN user_workbook_upload_access a ON a.country_id=cn.id
        JOIN users u ON u.id=a.user_id
        WHERE u.id=? AND u.role='FULL_ACCESS' AND u.status='ACTIVE' ORDER BY cn.name""",(user_id,)).fetchall()
