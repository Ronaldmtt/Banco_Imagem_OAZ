import sqlite3

def migrate():
    conn = sqlite3.connect('instance/oaz_img.db')
    cursor = conn.cursor()
    
    # Add new columns for AI-extracted attributes
    columns_to_add = [
        ('ai_item_type', 'VARCHAR(100)'),
        ('ai_color', 'VARCHAR(50)'),
        ('ai_material', 'VARCHAR(50)'),
        ('ai_pattern', 'VARCHAR(50)'),
        ('ai_style', 'VARCHAR(50)')
    ]
    
    for column_name, column_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE image ADD COLUMN {column_name} {column_type}")
            print(f"Successfully added '{column_name}' column to 'image' table.")
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e).lower():
                print(f"Column '{column_name}' already exists, skipping.")
            else:
                print(f"Error adding '{column_name}' column: {e}")

    conn.commit()
    conn.close()
    print("\n✅ Migration completed successfully!")

if __name__ == '__main__':
    migrate()
