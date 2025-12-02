# Tutorial Completo - OAZ Smart Image Bank

## Guia Passo a Passo para Testar Todas as Funcionalidades

---

## ANTES DE COMEÇAR

### Credenciais de Acesso
- **Usuário:** admin
- **Senha:** admin

### Requisitos para Análise IA
Para usar a análise automática de imagens, você precisa de uma chave de API da OpenAI. Sem ela, as imagens serão salvas mas sem análise automática.

---

## PARTE 1: LOGIN E PRIMEIRO ACESSO

### Passo 1.1 - Acessar o Sistema
1. Abra o aplicativo (clique em "Open website" ou acesse a URL do projeto)
2. Você verá a tela de login

### Passo 1.2 - Fazer Login
1. No campo **Usuário**, digite: `admin`
2. No campo **Senha**, digite: `admin`
3. Clique no botão **Entrar**
4. Você será redirecionado para o **Painel Principal (Dashboard)**

---

## PARTE 2: CONHECENDO O PAINEL PRINCIPAL

### O que você verá no Dashboard:
- **Estatísticas gerais:** Total de imagens, aprovadas, pendentes, rejeitadas
- **Imagens recentes:** Miniaturas das últimas imagens cadastradas
- **Atividade recente:** Log das últimas ações no sistema

### Navegação pela Sidebar (Menu Lateral):
A barra lateral contém todos os módulos do sistema:

| Ícone | Menu | Função |
|-------|------|--------|
| 📊 | Painel | Dashboard com estatísticas |
| 🖼️ | Biblioteca | Catálogo de todas as imagens |
| ⬆️ | Upload | Enviar novas imagens |
| 🏷️ | Coleções | Gerenciar coleções |
| 🏢 | Marcas | Gerenciar marcas |
| 📦 | Produtos | Cadastro de produtos (SKU) |
| 🛒 | Carteira | Carteira de compras |
| 🔍 | Auditoria | Relatórios de auditoria |
| 📈 | Relatórios | Métricas e exportações |
| ⚙️ | Configurações | API Key e configurações |

---

## PARTE 3: CONFIGURAR API OPENAI (OPCIONAL, MAS RECOMENDADO)

### Passo 3.1 - Acessar Configurações
1. No menu lateral, clique em **Configurações** (ícone de engrenagem)

### Passo 3.2 - Inserir Chave API
1. No campo **Chave da API OpenAI**, cole sua chave (começa com `sk-...`)
2. Clique em **Salvar Configurações**
3. Uma mensagem de sucesso aparecerá

> **Nota:** Sem a chave, o upload de imagens funcionará, mas não terá análise automática de IA.

---

## PARTE 4: CRIAR MARCAS

### Por que criar marcas primeiro?
As marcas são usadas para categorizar imagens e produtos. Crie-as antes de fazer uploads.

### Passo 4.1 - Acessar Marcas
1. No menu lateral, clique em **Marcas**

### Passo 4.2 - Criar Nova Marca
1. Clique no botão **+ Nova Marca** (canto superior direito)
2. Preencha os campos:
   - **Nome:** Ex: `OAZ Fashion`
   - **Descrição:** Ex: `Marca principal de moda feminina`
3. Clique em **Salvar**

### Sugestão de Marcas para Teste:
| Nome | Descrição |
|------|-----------|
| OAZ Fashion | Marca principal de moda feminina |
| OAZ Premium | Linha premium e luxo |
| OAZ Basic | Linha básica e casual |

### Passo 4.3 - Editar/Excluir Marca
- Para **editar**: clique no ícone de lápis ao lado da marca
- Para **excluir**: clique no ícone de lixeira (só funciona se não houver produtos vinculados)

---

## PARTE 5: CRIAR COLEÇÕES

### Por que criar coleções?
Coleções organizam as imagens por temporada, campanha ou tema.

### Passo 5.1 - Acessar Coleções
1. No menu lateral, clique em **Coleções**

### Passo 5.2 - Criar Nova Coleção
1. Clique no botão **+ Nova Coleção**
2. Preencha os campos:
   - **Nome:** Ex: `Verão 2025`
   - **Ano:** Ex: `2025`
   - **Estação:** Selecione uma opção (Primavera/Verão, Outono/Inverno, etc.)
   - **Campanha:** Ex: `Lançamento Verão`
   - **Descrição:** Ex: `Coleção de verão com peças leves e coloridas`
3. Clique em **Salvar**

### Sugestão de Coleções para Teste:
| Nome | Ano | Estação | Campanha |
|------|-----|---------|----------|
| Verão 2025 | 2025 | Primavera/Verão | Lançamento Verão |
| Inverno 2025 | 2025 | Outono/Inverno | Alto Inverno |
| Basics 2025 | 2025 | Atemporal | Coleção Permanente |

---

## PARTE 6: UPLOAD DE IMAGENS

### Passo 6.1 - Acessar Upload
1. No menu lateral, clique em **Upload**

### Passo 6.2 - Selecionar Imagem
1. Clique na área de upload (retângulo com linha pontilhada)
2. OU arraste uma imagem para a área
3. Formatos aceitos: **PNG, JPG, JPEG, GIF**
4. A prévia da imagem aparecerá

### Passo 6.3 - Preencher Metadados
Antes de enviar, preencha os campos opcionais:
- **Coleção:** Selecione uma coleção criada anteriormente
- **Marca:** Selecione uma marca
- **Fotógrafo:** Ex: `João Silva`
- **Data do Shooting:** Selecione a data da sessão de fotos

### Passo 6.4 - Enviar
1. Clique no botão **Enviar Imagem**
2. Aguarde o processamento (pode levar alguns segundos se a IA estiver ativa)
3. Você será redirecionado para a página de detalhes da imagem

### O que a IA analisa automaticamente:
- Tipo de peça (vestido, blusa, calça, etc.)
- Cores predominantes
- Material/tecido
- Padrões e estampas
- Estilo (casual, formal, esportivo)
- Tags para SEO
- Descrição detalhada em português

---

## PARTE 7: CATÁLOGO DE IMAGENS (BIBLIOTECA)

### Passo 7.1 - Acessar Biblioteca
1. No menu lateral, clique em **Biblioteca** (ou Biblioteca de Imagens)

### Passo 7.2 - Visualizar Imagens
- Você verá um grid com todas as imagens cadastradas
- Cada card mostra: miniatura, SKU, status (Pendente/Aprovado/Rejeitado)

### Passo 7.3 - Usar Filtros
Na barra superior, você pode filtrar por:
- **Busca:** Digite parte do SKU ou descrição
- **Status:** Todos, Pendente, Aprovado, Rejeitado
- **Coleção:** Selecione uma coleção específica
- **Marca:** Selecione uma marca específica

Clique em **Filtrar** para aplicar.

### Passo 7.4 - Ver Detalhes
1. Clique em qualquer imagem
2. Você verá a página de detalhes com:
   - Imagem em tamanho maior
   - Todas as informações extraídas pela IA
   - Peças detectadas (se houver múltiplas)
   - Botões de ação

### Passo 7.5 - Aprovar ou Rejeitar Imagem
Na página de detalhes:
1. Para **aprovar**: clique no botão verde **Aprovar**
2. Para **rejeitar**: clique no botão vermelho **Rejeitar**
3. O status mudará automaticamente

### Passo 7.6 - Re-analisar com IA
Se quiser uma nova análise:
1. Na página de detalhes, clique em **Re-analisar com IA**
2. Aguarde o processamento
3. Os dados serão atualizados

---

## PARTE 8: EDITAR IMAGEM

### Passo 8.1 - Acessar Edição
1. Na página de detalhes da imagem, clique em **Editar**
2. OU na biblioteca, clique no ícone de lápis da imagem

### Passo 8.2 - Campos Editáveis
Você pode editar:
- **SKU:** Código único do produto
- **Descrição:** Descrição detalhada
- **Coleção:** Vincular a outra coleção
- **Marca:** Vincular a outra marca
- **Fotógrafo:** Nome do fotógrafo
- **Data do Shooting:** Data da sessão
- **Status:** Pendente, Aprovado ou Rejeitado

### Passo 8.3 - Salvar
1. Faça as alterações desejadas
2. Clique em **Salvar Alterações**
3. Você será redirecionado para a página de detalhes

---

## PARTE 9: CADASTRO DE PRODUTOS

### O que são Produtos?
Produtos são itens com SKU único que podem ter uma ou mais imagens associadas. Diferente das imagens, produtos contêm informações comerciais.

### Passo 9.1 - Acessar Produtos
1. No menu lateral, clique em **Produtos**

### Passo 9.2 - Criar Novo Produto
1. Clique no botão **+ Novo Produto**
2. Preencha os campos:

| Campo | Exemplo | Obrigatório |
|-------|---------|-------------|
| SKU | OAZ-VES-001 | Sim |
| Descrição | Vestido longo estampado | Sim |
| Cor | Azul Marinho | Não |
| Categoria | Vestidos | Não |
| Marca | OAZ Fashion | Não |
| Coleção | Verão 2025 | Não |
| Atributos Técnicos | Tecido: Viscose, Forro: Sim | Não |

3. Clique em **Salvar**

### Sugestão de Produtos para Teste:
```
SKU: OAZ-VES-001
Descrição: Vestido longo floral
Cor: Azul com flores
Categoria: Vestidos
Atributos: Tecido: Viscose, Comprimento: Longo, Decote: V

SKU: OAZ-BLU-002
Descrição: Blusa manga bufante
Cor: Branco
Categoria: Blusas
Atributos: Tecido: Algodão, Manga: Bufante, Gola: Redonda

SKU: OAZ-CAL-003
Descrição: Calça wide leg
Cor: Preto
Categoria: Calças
Atributos: Tecido: Alfaiataria, Cintura: Alta, Modelagem: Wide
```

### Passo 9.3 - Buscar Produtos
- Use o campo de busca para pesquisar por SKU, descrição, cor ou categoria
- Clique em **Buscar**

### Passo 9.4 - Filtrar por Status de Foto
- Use o filtro **Status de Foto** para ver:
  - Todos
  - Com Foto
  - Sem Foto

### Passo 9.5 - Exportar Produtos
- Clique no botão **Exportar CSV** para baixar uma planilha com todos os produtos

---

## PARTE 10: CARTEIRA DE COMPRAS

### O que é a Carteira de Compras?
É uma lista de SKUs importados de um arquivo CSV que representa os produtos planejados para compra. O sistema cruza automaticamente com as imagens existentes.

### Passo 10.1 - Acessar Carteira
1. No menu lateral, clique em **Carteira de Compras**

### Passo 10.2 - Importar CSV

#### Formato do Arquivo CSV:
O arquivo deve ter estas colunas (separadas por vírgula ou ponto-e-vírgula):

```csv
sku,descricao,quantidade,data_entrega,fornecedor
OAZ-VES-001,Vestido longo floral,50,2025-03-15,Fornecedor A
OAZ-BLU-002,Blusa manga bufante,100,2025-03-20,Fornecedor B
OAZ-CAL-003,Calça wide leg,80,2025-03-25,Fornecedor C
OAZ-SAI-004,Saia midi plissada,60,2025-04-01,Fornecedor A
OAZ-JAQ-005,Jaqueta jeans,40,2025-04-10,Fornecedor D
```

#### Para Importar:
1. Clique no botão **Importar CSV**
2. Clique em **Escolher Arquivo** e selecione seu CSV
3. Clique em **Importar**
4. O sistema processará e mostrará quantos registros foram importados

### Passo 10.3 - Visualizar Carteira
Após importar, você verá:
- Lista de todos os SKUs da carteira
- **Status de Foto:** Com Foto (verde), Sem Foto (vermelho), Pendente (amarelo)
- O status é atualizado automaticamente baseado nas imagens existentes

### Passo 10.4 - Cruzar com Imagens
1. Clique no botão **Cruzar com Imagens**
2. O sistema verifica quais SKUs da carteira já possuem fotos
3. Os status são atualizados automaticamente

### Passo 10.5 - Filtrar Carteira
Use os filtros para ver:
- Todos os itens
- Apenas **Com Foto**
- Apenas **Sem Foto**
- Apenas **Pendente**

---

## PARTE 11: AUDITORIA

### Passo 11.1 - Acessar Auditoria
1. No menu lateral, clique em **Auditoria**

### O que você verá:

#### Seção 1: Status dos Produtos
- **Total de Produtos:** Quantidade total cadastrada
- **Produtos com Foto:** Quantidade que tem imagem vinculada (verde)
- **Produtos sem Foto:** Quantidade pendente de foto (vermelho)

#### Seção 2: Status da Carteira
- **Total na Carteira:** Itens importados
- **Com Foto:** SKUs que já possuem imagem
- **Sem Foto:** SKUs que precisam de foto
- **Pendente:** Aguardando verificação

#### Seção 3: Alterações de SKU
- Histórico das últimas mudanças de nomenclatura de SKU
- Mostra: SKU antigo → SKU novo → Data

#### Seção 4: Divergências
- SKUs na carteira que foram alterados no cadastro de produtos
- Indica inconsistências entre carteira e cadastro atual

#### Seção 5: SKUs Pendentes
- Lista de produtos sem foto
- Permite ver a lista completa ou exportar CSV

### Passo 11.2 - Exportar Relatórios
Clique nos botões de exportação:
- **Exportar lista** (Produtos com foto)
- **Exportar CSV** (SKUs pendentes)
- **Ver histórico completo** (Alterações de SKU)

### Passo 11.3 - Ver SKUs Pendentes
1. Clique em **Ver Todos** na seção de SKUs Pendentes
2. Você verá a lista completa de produtos sem foto
3. Use para priorizar os próximos shootings

---

## PARTE 12: RELATÓRIOS

### Passo 12.1 - Acessar Relatórios
1. No menu lateral, clique em **Relatórios**

### O que você verá:

#### Métricas Gerais
- Total de imagens
- Distribuição por status (gráfico)
- Imagens por coleção
- Imagens por marca

#### Exportações Disponíveis
- **Exportar Todas as Imagens (CSV)**
- **Exportar por Status (CSV)**
- **Exportar por Coleção (CSV)**

### Passo 12.2 - Exportar CSV
1. Escolha o tipo de exportação desejado
2. Clique no botão correspondente
3. O download começará automaticamente

---

## PARTE 13: FLUXO DE TRABALHO COMPLETO (EXEMPLO PRÁTICO)

### Cenário: Nova Coleção Verão 2025

#### Dia 1: Preparação
1. Login no sistema
2. Configurar API OpenAI (se ainda não fez)
3. Criar Marca: "OAZ Fashion"
4. Criar Coleção: "Verão 2025" (Ano: 2025, Estação: Primavera/Verão)

#### Dia 2: Cadastro de Produtos
1. Cadastrar produtos da coleção:
   - OAZ-VES-001: Vestido longo floral
   - OAZ-BLU-002: Blusa manga bufante
   - OAZ-CAL-003: Calça wide leg
2. Importar CSV da Carteira de Compras com os SKUs planejados

#### Dia 3: Shooting de Fotos
1. Realizar sessão de fotos
2. Fazer upload das imagens no sistema
3. Vincular cada imagem ao produto/SKU correspondente
4. A IA irá analisar automaticamente cada peça

#### Dia 4: Revisão e Aprovação
1. Acessar Biblioteca de Imagens
2. Filtrar por Status: Pendente
3. Revisar cada imagem:
   - Verificar se a análise da IA está correta
   - Editar se necessário
   - Aprovar ou Rejeitar

#### Dia 5: Auditoria e Relatórios
1. Acessar Auditoria
2. Verificar quantos SKUs ainda estão sem foto
3. Exportar lista de pendentes para priorizar próximo shooting
4. Acessar Relatórios
5. Exportar relatório completo da coleção

---

## ARQUIVOS DE EXEMPLO PARA IMPORTAÇÃO

### Arquivo: carteira_exemplo.csv
```csv
sku,descricao,quantidade,data_entrega,fornecedor
OAZ-VES-001,Vestido longo floral,50,2025-03-15,Têxtil ABC
OAZ-VES-002,Vestido midi estampado,30,2025-03-15,Têxtil ABC
OAZ-BLU-001,Blusa cropped,100,2025-03-20,Confecções XYZ
OAZ-BLU-002,Blusa manga bufante,80,2025-03-20,Confecções XYZ
OAZ-CAL-001,Calça wide leg,60,2025-03-25,Jeans Master
OAZ-CAL-002,Calça skinny,70,2025-03-25,Jeans Master
OAZ-SAI-001,Saia midi plissada,40,2025-04-01,Têxtil ABC
OAZ-JAQ-001,Jaqueta jeans oversized,25,2025-04-10,Jeans Master
OAZ-MAC-001,Macacão longo,35,2025-04-10,Confecções XYZ
OAZ-SHO-001,Short alfaiataria,90,2025-03-30,Confecções XYZ
```

### Como usar este arquivo:
1. Copie o conteúdo acima
2. Cole em um editor de texto (Bloco de Notas, VS Code)
3. Salve como `carteira_exemplo.csv`
4. Importe no sistema via **Carteira > Importar CSV**

---

## DICAS E BOAS PRÁTICAS

### Nomenclatura de SKU
Sugestão de padrão:
`MARCA-CATEGORIA-NUMERO`

Exemplos:
- OAZ-VES-001 (Vestido 001)
- OAZ-BLU-002 (Blusa 002)
- OAZ-CAL-003 (Calça 003)

### Organização de Coleções
- Use o campo **Ano** para facilitar buscas futuras
- Use **Estação** para agrupar por temporada
- Use **Campanha** para identificar lançamentos específicos

### Qualidade das Imagens
Para melhor análise da IA:
- Use fundo neutro (branco ou cinza)
- Boa iluminação
- Peça centralizada
- Resolução mínima: 800x800 pixels

### Workflow de Aprovação
1. **Pendente:** Imagem recém-enviada, aguardando revisão
2. **Aprovado:** Imagem validada e pronta para uso
3. **Rejeitado:** Imagem com problemas (qualidade, erro, etc.)

---

## SOLUÇÃO DE PROBLEMAS

### Problema: IA não está analisando as imagens
**Solução:** Verifique se a API Key da OpenAI está configurada em Configurações.

### Problema: Erro ao importar CSV
**Solução:** Verifique se o arquivo está no formato correto (UTF-8) e se as colunas estão separadas por vírgula.

### Problema: Imagem não aparece após upload
**Solução:** Aguarde alguns segundos e atualize a página. Se persistir, verifique o formato da imagem (PNG, JPG, JPEG, GIF).

### Problema: Status da carteira não atualiza
**Solução:** Clique no botão "Cruzar com Imagens" para forçar a atualização.

---

## SUPORTE

Para resetar a senha do admin:
```bash
python reset_admin.py
```

Para migrar o banco de dados após atualizações:
```bash
python migrate_db.py
```

---

**Fim do Tutorial**

*OAZ Smart Image Bank - Sistema de Gerenciamento Inteligente de Imagens*
