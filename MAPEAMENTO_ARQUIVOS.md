# Mapeamento dos Arquivos de Importação

## Resumo dos Arquivos Recebidos

| Arquivo | Descrição | Total de SKUs |
|---------|-----------|---------------|
| RelaçãoSKUsHOMESOUQVERAO.xlsx | SKUs de HOME Verão 24-25 | 274 |
| RelaçãoSKUsSOUQFotografadosMODA.xlsx | SKUs Moda Fotografados | 404 (288 Verão + 116 Alto Verão) |
| CARTEIRA_INVERNO_26_-_IMAGEM.xlsx | Carteira Completa Inverno 26 | 344 (289 Roupas + 48 Home + 7 Acessórios) |
| RelaçãoFotosAcessVerão.xlsx | Fotos Acessórios Verão | 298 |

**Total Geral: 1.320 registros**

---

## Estrutura Detalhada por Arquivo

### 1. RelaçãoSKUsHOMESOUQVERAO.xlsx

**Propósito:** Lista de SKUs de produtos HOME que precisam de foto para a coleção Verão 24-25

| Aba | Conteúdo |
|-----|----------|
| VERÃO | Lista simples de SKUs |

**Formato do SKU:** `XX.XX.XX.XXX.XXX` (ex: 01.03.25.346.011)

**Colunas:**
- Coluna única com SKUs

**Prefixos identificados:**
- `01.XX` = Produtos HOME

---

### 2. RelaçãoSKUsSOUQFotografadosMODA.xlsx

**Propósito:** SKUs de moda que já foram fotografados

| Aba | Conteúdo | Registros |
|-----|----------|-----------|
| VERÃO | SKUs fotografados Verão 24-25 | 288 |
| Consulta | Metadados de arquivos de foto | 116 |
| ALTO VERÃO | SKUs fotografados Alto Verão | 116 |

**Formato do SKU:** `XX.XX.XX.XXX.XXX` (ex: 04.23.02.581.007)

**Prefixos identificados:**
- `04.23.XX` = SOUQ Moda
- `04.26.XX` = SOUQ Moda (variante)

**Aba "Consulta" contém:**
- Nome do arquivo da foto
- Data de acesso/modificação/criação
- Caminho da pasta

---

### 3. CARTEIRA_INVERNO_26_-_IMAGEM.xlsx (PRINCIPAL)

**Propósito:** Carteira de compras completa do Inverno 2026 com todos os metadados

| Aba | Conteúdo | Registros |
|-----|----------|-----------|
| ROUPAS | Roupas Inverno 26 | 289 |
| HOME | Produtos Home Inverno 26 | 48 |
| ACESSÓRIOS | Acessórios Inverno 26 | 7 |

**Formato do SKU:** `XX.XX.XX.XXXX.XXX` (ex: 04.23.02.0001.053)

#### Colunas Detalhadas:

| Coluna | Tipo | Descrição | Exemplo |
|--------|------|-----------|---------|
| REFERÊNCIA E COR | Texto | SKU completo do produto | 04.23.02.0001.053 |
| NOME | Texto | Nome do produto | BLUSA LOLA BLACK |
| NOME / COR | Texto | Cor/Estampa | LISTRADO, ESTAMPADO |
| GRUPO | Texto | Categoria principal | MALHA, TECIDO, BIJOUX |
| SUBGRUPO | Texto | Subcategoria | BLUSA, CALÇA, VESTIDO, PULSEIRA |
| ENTRADA | Texto | Coleção/Temporada | INVERNO 2026 |
| ESTILISTA | Texto | Responsável pelo produto | 28 THAIS CAMPIONE... |
| FOTO | Texto | Status da foto | SIM, NÃO, SIM - MIGRADO |
| QUANDO | Texto | Shooting planejado | SHOOTING INVERNO 26 |
| OKR | Texto | Aprovação | OK |
| OBS | Texto | Observações | Notas diversas |
| NACIONAL / IMPORTADO | Texto | Origem | NACIONAL, IMPORTADO |

---

### 4. RelaçãoFotosAcessVerão.xlsx

**Propósito:** Lista de SKUs de acessórios com foto para Verão 24/25

| Aba | Conteúdo |
|-----|----------|
| VERÃO | Lista de SKUs de acessórios |

**Formato do SKU:** `XX.XX.XX.XXX.XXX` (ex: 02.16.03.205.035)

**Prefixos identificados:**
- `02.09.XX` = Bijoux
- `02.12.XX` = Bolsas
- `02.16.XX` = Outros acessórios

---

## Decodificação do Formato de SKU

O SKU segue o padrão: `GG.SS.TT.RRRR.CCC`

| Posição | Significado | Exemplos |
|---------|-------------|----------|
| GG | Grupo/Divisão | 01=HOME, 02=Acessórios, 04=Moda |
| SS | Subgrupo | 23=SOUQ, 26=Variante |
| TT | Tipo | 02=Blusa, 03=Calça, etc |
| RRRR | Referência | Número sequencial |
| CCC | Cor | 053=Listrado, 054=Estampado |

---

## Mapeamento para o Sistema OAZ

### Campos da Carteira de Compras

| Campo Excel | Campo Sistema | Tipo |
|-------------|--------------|------|
| REFERÊNCIA E COR | sku | String |
| NOME | descricao | String |
| NOME / COR | cor | String |
| GRUPO | categoria | String |
| SUBGRUPO | subcategoria | String |
| ENTRADA | colecao | String |
| ESTILISTA | estilista | String |
| FOTO | status_foto | String (Com Foto/Sem Foto/Pendente) |
| QUANDO | shooting | String |
| OBS | observacoes | String |
| NACIONAL / IMPORTADO | origem | String |

### Regras de Importação

1. **Status da Foto:**
   - "SIM" → "Com Foto"
   - "NÃO" ou vazio → "Sem Foto"
   - "SIM - MIGRADO" → "Com Foto"

2. **SKU:**
   - Remover espaços extras
   - Normalizar formato (remover .00 final se existir)

3. **Validação:**
   - SKU é obrigatório
   - Ignorar linhas com SKU vazio

---

## Arquivos para Importação Recomendada

### Ordem de Importação:

1. **CARTEIRA_INVERNO_26** (Primeiro - é a carteira principal)
   - Importar aba ROUPAS
   - Importar aba HOME
   - Importar aba ACESSÓRIOS

2. **Relações de SKUs** (Segundo - para cruzar com fotos existentes)
   - RelaçãoSKUsHOMESOUQVERAO
   - RelaçãoSKUsSOUQFotografadosMODA
   - RelaçãoFotosAcessVerão

### Fluxo de Trabalho:

```
1. Importar Carteira Inverno 26 (todos os SKUs planejados)
       ↓
2. Cruzar com SKUs Fotografados (atualizar status_foto)
       ↓
3. Gerar relatório de divergências
       ↓
4. Identificar SKUs pendentes de foto
```

---

## Exemplo de Dados para Teste

### Amostra da Carteira (ROUPAS):

| SKU | Nome | Cor | Grupo | Subgrupo | Entrada |
|-----|------|-----|-------|----------|---------|
| 04.23.02.0001.053 | BLUSA LOLA BLACK | LISTRADO | MALHA | BLUSA | INVERNO 2026 |
| 04.23.02.0002.053 | BLUSA LOLA MARINE | LISTRADO | MALHA | BLUSA | INVERNO 2026 |
| 04.23.02.0003.054 | BLUSA DIARA BOTARA | ESTAMPADO | MALHA | BLUSA | INVERNO 2026 |

### Amostra da Carteira (ACESSÓRIOS):

| SKU | Nome | Cor | Grupo | Subgrupo |
|-----|------|-----|-------|----------|
| 02.09.05.780.011.00 | PULSEIRA IMANI | DOURADO | BIJOUX | PULSEIRA |
| 02.09.05.781.011.00 | PULSEIRA SUKI | DOURADO | BIJOUX | PULSEIRA |
| 02.09.06.191.018.00 | COLAR MAKENA | PRATA ENVELHECIDA | BIJOUX | COLAR |

---

## Próximos Passos

1. Atualizar o sistema para aceitar arquivos .xlsx
2. Adicionar novos campos ao modelo CarteiraCompras
3. Criar tela de importação com seleção de aba
4. Implementar validação de formato de SKU
5. Criar relatório de cruzamento entre carteira e fotos
