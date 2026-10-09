import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "jobfeed.db")

def migrate():
    print(f"Migrating {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if columns exist
    cursor.execute("PRAGMA table_info(search_profiles)")
    columns = [row[1] for row in cursor.fetchall()]
    
    dropped = False
    
    if "location_mode" in columns:
        print("Dropping location_mode column...")
        cursor.execute("ALTER TABLE search_profiles DROP COLUMN location_mode")
        dropped = True
        
    if "onsite_area" in columns:
        print("Dropping onsite_area column...")
        cursor.execute("ALTER TABLE search_profiles DROP COLUMN onsite_area")
        dropped = True
        
    if dropped:
        conn.commit()
        print("Migration complete.")
    else:
        print("Columns already dropped or do not exist.")
        
    conn.close()

if __name__ == "__main__":
    migrate()
