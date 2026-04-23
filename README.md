# Conciliação Bancária 🏦

Sistema para comparar extratos bancários com a planilha de pagamentos e identificar divergências.

## Como rodar localmente

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Rodar o sistema
```bash
streamlit run app.py
```
O sistema abrirá automaticamente no navegador em `http://localhost:8501`

---

## Como usar

1. **Carregue a planilha de pagamentos** (ex: `Pagos_2026-04-20.xlsx`)
2. **Selecione o banco** — o sistema detecta automaticamente os bancos presentes na planilha
3. **Selecione as contas bancárias** da planilha que correspondem ao extrato
4. **Carregue o extrato** CSV do banco selecionado
5. **Clique em "Conciliar agora"**

---

## Bancos suportados

| Banco | Status |
|-------|--------|
| Bradesco | ✅ Suportado |
| Itaú | ✅ Suportado |
| Santander | ✅ Suportado |
| Banco do Brasil | ✅ Suportado |
| Outros | ⚠️ Detecção automática (pode precisar ajuste) |

---

## Resultado da conciliação

| Indicador | Significado |
|-----------|-------------|
| ✅ Conciliado | Débito do extrato encontrado na planilha (data e valor exatos) |
| ⚠️ Data próxima | Encontrado com diferença de ±1 dia na data |
| ❌ Extrato sem planilha | Débito no extrato que **não existe** na planilha |
| 🔍 Planilha sem extrato | Pagamento na planilha que **não aparece** no extrato |

---

## Adicionar novo banco

Edite o dicionário `BANK_CONFIGS` e `CONTA_TO_BANK` no início do `app.py`:

```python
BANK_CONFIGS["Caixa"] = {
    "encoding": "latin-1",
    "delimiter": ";",
    ...
}

CONTA_TO_BANK["Caixa"] = ["Caixa", "CEF", "Caixa Econômica"]
```

---

## Requisitos
- Python 3.10+
- Planilha de pagamentos com coluna **"Conta Bancária"**
- Extrato bancário em formato **CSV**
