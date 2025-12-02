import sqlite3

def migrate():
    conn = sqlite3.connect('instance/oaz_img.db')
    cursor = conn.cursor()
    
    # Add campanha column to Collection table
    try:
        cursor.execute("ALTER TABLE collection ADD COLUMN campanha VARCHAR(100)")
        print("Successfully added 'campanha' column to 'collection' table.")
    except sqlite3.OperationalError as e:
        if 'duplicate column name' in str(e).lower():
            print("Column 'campanha' already exists in 'collection', skipping.")
        else:
            print(f"Error adding 'campanha' column: {e}")
    
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
    
    # Create Produto table
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS produto (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku VARCHAR(50) UNIQUE NOT NULL,
                descricao VARCHAR(255) NOT NULL,
                cor VARCHAR(50),
                categoria VARCHAR(100),
                atributos_tecnicos TEXT,
                marca_id INTEGER,
                colecao_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                tem_foto BOOLEAN DEFAULT 0,
                ativo BOOLEAN DEFAULT 1,
                FOREIGN KEY (marca_id) REFERENCES brand (id),
                FOREIGN KEY (colecao_id) REFERENCES collection (id)
            )
        ''')
        print("Successfully created 'produto' table.")
    except sqlite3.OperationalError as e:
        print(f"Error creating 'produto' table: {e}")
    
    # Create ImagemProduto table (association between Image and Produto)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS imagem_produto (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                imagem_id INTEGER NOT NULL,
                produto_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (imagem_id) REFERENCES image (id) ON DELETE CASCADE,
                FOREIGN KEY (produto_id) REFERENCES produto (id) ON DELETE CASCADE
            )
        ''')
        print("Successfully created 'imagem_produto' table.")
    except sqlite3.OperationalError as e:
        print(f"Error creating 'imagem_produto' table: {e}")
    
    # Create HistoricoSKU table
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historico_sku (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id INTEGER NOT NULL,
                sku_antigo VARCHAR(50) NOT NULL,
                sku_novo VARCHAR(50) NOT NULL,
                data_alteracao DATETIME DEFAULT CURRENT_TIMESTAMP,
                motivo VARCHAR(255),
                usuario_id INTEGER,
                FOREIGN KEY (produto_id) REFERENCES produto (id),
                FOREIGN KEY (usuario_id) REFERENCES user (id)
            )
        ''')
        print("Successfully created 'historico_sku' table.")
    except sqlite3.OperationalError as e:
        print(f"Error creating 'historico_sku' table: {e}")
    
    # Create CarteiraCompras table
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS carteira_compras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku VARCHAR(50) NOT NULL,
                descricao VARCHAR(255),
                cor VARCHAR(50),
                categoria VARCHAR(100),
                quantidade INTEGER DEFAULT 1,
                status_foto VARCHAR(20) DEFAULT 'Pendente',
                produto_id INTEGER,
                data_importacao DATETIME DEFAULT CURRENT_TIMESTAMP,
                lote_importacao VARCHAR(50),
                FOREIGN KEY (produto_id) REFERENCES produto (id)
            )
        ''')
        print("Successfully created 'carteira_compras' table.")
    except sqlite3.OperationalError as e:
        print(f"Error creating 'carteira_compras' table: {e}")
    
    # Create indexes for faster queries
    indexes = [
        ('idx_image_item_image_id', 'image_item', 'image_id'),
        ('idx_produto_sku', 'produto', 'sku'),
        ('idx_produto_marca', 'produto', 'marca_id'),
        ('idx_produto_colecao', 'produto', 'colecao_id'),
        ('idx_imagem_produto_imagem', 'imagem_produto', 'imagem_id'),
        ('idx_imagem_produto_produto', 'imagem_produto', 'produto_id'),
        ('idx_historico_sku_produto', 'historico_sku', 'produto_id'),
        ('idx_carteira_sku', 'carteira_compras', 'sku'),
        ('idx_carteira_status', 'carteira_compras', 'status_foto'),
    ]
    
    for idx_name, table, column in indexes:
        try:
            cursor.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})')
            print(f"Successfully created index '{idx_name}'.")
        except sqlite3.OperationalError as e:
            print(f"Error creating index '{idx_name}': {e}")

    conn.commit()
    conn.close()
    print("\n✅ Migration completed successfully!")

if __name__ == '__main__':
    migrate()
