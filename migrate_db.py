import sqlite3

def migrate():
    conn = sqlite3.connect('instance/oaz_img.db')
    cursor = conn.cursor()
    
    # Add new columns for AI-extracted attributes to Image table
    image_columns_to_add = [
        ('ai_item_type', 'VARCHAR(100)'),
        ('ai_color', 'VARCHAR(50)'),
        ('ai_material', 'VARCHAR(100)'),
        ('ai_pattern', 'VARCHAR(50)'),
        ('ai_style', 'VARCHAR(50)')
    ]
    
    for column_name, column_type in image_columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE image ADD COLUMN {column_name} {column_type}")
            print(f"Successfully added '{column_name}' column to 'image' table.")
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e).lower():
                print(f"Column '{column_name}' already exists, skipping.")
            else:
                print(f"Error adding '{column_name}' column: {e}")

    # Create ImageItem table for multiple pieces per image
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS image_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_id INTEGER NOT NULL,
                item_order INTEGER DEFAULT 1,
                description TEXT,
                tags TEXT,
                ai_item_type VARCHAR(100),
                ai_color VARCHAR(50),
                ai_material VARCHAR(100),
                ai_pattern VARCHAR(50),
                ai_style VARCHAR(50),
                position_ref VARCHAR(50),
                FOREIGN KEY (image_id) REFERENCES image (id) ON DELETE CASCADE
            )
        ''')
        print("Successfully created 'image_item' table.")
    except sqlite3.OperationalError as e:
        print(f"Error creating 'image_item' table: {e}")
    
    # Create index for faster queries
    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_image_item_image_id ON image_item(image_id)')
        print("Successfully created index on 'image_item.image_id'.")
    except sqlite3.OperationalError as e:
        print(f"Error creating index: {e}")

    conn.commit()
    conn.close()
    print("\n✅ Migration completed successfully!")

if __name__ == '__main__':
    migrate()
