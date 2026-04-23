import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import re
from datetime import timedelta
import xlsxwriter

# ──────────────────────────────────────────────────────────────
# Configuração por banco
# ──────────────────────────────────────────────────────────────

BANK_CONFIGS = {
    "Bradesco": {
        "encoding": "latin-1",
        "delimiter": ";",
        "date_col": "Data",
        "desc_col": "Lançamento",
        "debit_col": "Débito (R$)",
        "credit_col": "Crédito (R$)",
        "doc_col": "Dcto.",
        "date_fmt": "%d/%m/%Y",
        "decimal": ",",
        "thousands": ".",
        "skip_rows": "auto",   
    },
    "Itaú": {
        "encoding": "latin-1",
        "delimiter": ";",
        "date_col": "Data",
        "desc_col": "Histórico",
        "debit_col": "Valor",
        "credit_col": "Valor",
        "doc_col": "Documento",
        "date_fmt": "%d/%m/%Y",
        "decimal": ",",
        "thousands": ".",
        "skip_rows": "auto",
    },
    "Santander": {
        "encoding": "latin-1",
        "delimiter": ";",
        "date_col": "Data",
        "desc_col": "Descrição",
        "debit_col": "Débito",
        "credit_col": "Crédito",
        "doc_col": "Número do documento",
        "date_fmt": "%d/%m/%Y",
        "decimal": ",",
        "thousands": ".",
        "skip_rows": "auto",
    },
    "Banco do Brasil": {
        "encoding": "latin-1",
        "delimiter": ";",
        "date_col": "Data",
        "desc_col": "Histórico",
        "debit_col": "Débito (R$)",
        "credit_col": "Crédito (R$)",
        "doc_col": "Documento",
        "date_fmt": "%d/%m/%Y",
        "decimal": ",",
        "thousands": ".",
        "skip_rows": "auto",
    },
}

# Mapeamento: quais "Conta Bancária" da planilha pertencem a qual banco
CONTA_TO_BANK = {
    "Bradesco": ["Bradesco", "bradesco"],
    "Itaú": ["Itaú", "Itau", "itaú", "itau"],
    "Santander": ["Santander", "santander"],
    "Banco do Brasil": ["BB", "Banco do Brasil", "BancoBrasil"],
}

# ──────────────────────────────────────────────────────────────
# Utilitários
# ──────────────────────────────────────────────────────────────

def parse_br_number(s):
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    s = str(s).strip()
    if s in ("", "-", "--", "nan"):
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def detect_encoding(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


@st.cache_data
def parse_bank_csv(file_bytes: bytes, bank_name: str) -> pd.DataFrame | None:
    """
    Parser genérico para extratos bancários CSV.
    Localiza automaticamente a linha de cabeçalho e extrai os dados.
    Retorna DataFrame com colunas padronizadas: Data, Descricao, Debito, Credito, Documento
    Suporta arquivos com separadores \r (Bradesco) ou \n (padrão).
    """
    enc = detect_encoding(file_bytes)
    content = file_bytes.decode(enc, errors="replace")

    # Detecta separador de linha: \r ou \n
    cr_count = content.count("\r") - content.count("\r\n")
    lf_count = content.count("\n")
    if cr_count > lf_count:
        # Bradesco usa \r como separador; remove \n residuais
        content = content.replace("\r\n", "\r").replace("\n", "")
        lines = content.split("\r")
    else:
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = content.split("\n")

    
    header_keywords = {"data", "date", "histórico", "históric", "historico",
                       "lançamento", "lancamento", "descrição", "descricao",
                       "débito", "debito", "crédito", "credito"}

    header_idx = None
    for i, line in enumerate(lines):
        normalized = line.lower().replace(";", " ").replace(",", " ")
        if any(kw in normalized for kw in header_keywords):
            # Linha deve conter pelo menos 3 colunas e uma palavra de valor/data
            if line.count(";") >= 2 or line.count(",") >= 2:
                header_idx = i
                break

    if header_idx is None:
        return None

    header_line = lines[header_idx]
    delimiter = ";" if header_line.count(";") >= header_line.count(",") else ","
    headers = [h.strip().strip('"') for h in header_line.split(delimiter)]

    data_rows = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        parts = [p.strip().strip('"') for p in line.split(delimiter)]
        if len(parts) < 3:
            continue
        # Linha de dado: começa com uma data DD/MM/YYYY
        if re.match(r"\d{2}/\d{2}/\d{4}", parts[0]):
            # Preenche colunas faltantes
            while len(parts) < len(headers):
                parts.append("")
            data_rows.append(parts[:len(headers)])

    if not data_rows:
        return None

    df_raw = pd.DataFrame(data_rows, columns=headers)

    # ── Normaliza para colunas padrão ──────────────────────────
    col_lower = {c: c.lower().strip() for c in df_raw.columns}

    def find_col(*keywords):
        for c, cl in col_lower.items():
            for kw in keywords:
                if kw in cl:
                    return c
        return None

    date_col = find_col("data", "date")
    desc_col = find_col("lançamento", "lancamento", "histórico", "historico",
                        "descrição", "descricao", "memo")
    debit_col = find_col("débito", "debito", "saída", "saida", "debit")
    credit_col = find_col("crédito", "credito", "entrada", "entrada", "credit")
    doc_col = find_col("dcto", "documento", "doc", "número", "numero", "nº")

    if date_col is None:
        return None

    result_rows = []
    for _, row in df_raw.iterrows():
        date_str = str(row[date_col]).strip()
        try:
            date = pd.to_datetime(date_str, format="%d/%m/%Y")
        except Exception:
            try:
                date = pd.to_datetime(date_str, dayfirst=True)
            except Exception:
                continue

        desc = str(row[desc_col]).strip() if desc_col else ""
        doc = str(row[doc_col]).strip() if doc_col else ""

        # Débito / Crédito
        deb_raw = parse_br_number(row[debit_col]) if debit_col else None
        cred_raw = parse_br_number(row[credit_col]) if credit_col else None

        # Quando só existe coluna "Valor" (ex: Itaú), negativo = débito
        if debit_col and debit_col == credit_col and deb_raw is not None:
            if deb_raw < 0:
                deb_val = abs(deb_raw)
                cred_val = None
            else:
                deb_val = None
                cred_val = deb_raw
        else:
            deb_val = abs(deb_raw) if deb_raw is not None else None
            cred_val = abs(cred_raw) if cred_raw is not None else None

        result_rows.append({
            "Data": date,
            "Descricao": desc,
            "Documento": doc,
            "Debito": deb_val,
            "Credito": cred_val,
        })

    return pd.DataFrame(result_rows)


@st.cache_data
def load_payments_xlsx(file_bytes: bytes) -> pd.DataFrame:
    df = pd.read_excel(BytesIO(file_bytes))
    for col in ["Data de Vencimento", "Data de Pagamento"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
    if "Valor" in df.columns:
        df["_valor_num"] = pd.to_numeric(df["Valor"], errors="coerce")
    if "Valor Pago" in df.columns:
        df["_valor_pago_num"] = df["Valor Pago"].apply(
            lambda x: parse_br_number(x) if isinstance(x, str) else (x if pd.notna(x) else None)
        )
    return df


def get_banks_from_xlsx(df: pd.DataFrame) -> list[str]:
    if "Conta Bancária" not in df.columns:
        return []
    return sorted(df["Conta Bancária"].dropna().unique().tolist())


def detect_bank_from_conta(conta: str) -> str | None:
    """Detecta o banco a partir do nome da conta bancária."""
    for bank, keywords in CONTA_TO_BANK.items():
        for kw in keywords:
            if kw.lower() in conta.lower():
                return bank
    return None


def reconcile(
    bank_stmt: pd.DataFrame,
    payments_df: pd.DataFrame,
    selected_contas: list[str],
    tolerance: float = 0.05,
    date_tolerance_days: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Retorna:
      matched     — lançamentos do extrato encontrados na planilha
      unmatched   — lançamentos do extrato NÃO encontrados na planilha
      orphans     — registros da planilha NÃO encontrados no extrato
    """
    # Filtra planilha pelas contas selecionadas
    pay = payments_df[payments_df["Conta Bancária"].isin(selected_contas)].copy()

    # Só débitos no extrato
    stmt_debits = bank_stmt[bank_stmt["Debito"].notna() & (bank_stmt["Debito"] > 0)].copy()

    valor_col = "_valor_pago_num" if "_valor_pago_num" in pay.columns else "_valor_num"

    # Marca os registros da planilha como "usados"
    pay["_usado"] = False
    pay_idx = pay.index.tolist()

    matched_rows, unmatched_rows = [], []

    for _, stmt_row in stmt_debits.iterrows():
        stmt_date = stmt_row["Data"].date()
        stmt_val = round(stmt_row["Debito"], 2)

        best_match = None
        best_score = None  # (date_diff, val_diff)

        for idx in pay_idx:
            if pay.at[idx, "_usado"]:
                continue
            p_date = pay.at[idx, "Data de Pagamento"]
            p_val = pay.at[idx, valor_col]
            if pd.isna(p_date) or pd.isna(p_val):
                continue
            p_date = p_date.date()
            p_val = round(float(p_val), 2)

            date_diff = abs((stmt_date - p_date).days)
            val_diff = abs(stmt_val - p_val)

            if val_diff <= tolerance and date_diff <= date_tolerance_days:
                score = (date_diff, val_diff)
                if best_score is None or score < best_score:
                    best_match = idx
                    best_score = score

        if best_match is not None:
            pay.at[best_match, "_usado"] = True
            date_diff = abs((stmt_date - pay.at[best_match, "Data de Pagamento"].date()).days)
            status = "Conciliado" if date_diff == 0 else "Data próxima (±1 dia)"
            matched_rows.append({
                "Data Extrato": stmt_row["Data"].strftime("%d/%m/%Y"),
                "Descrição Extrato": stmt_row["Descricao"],
                "Documento": stmt_row["Documento"],
                "Débito (R$)": stmt_row["Debito"],
                "Fornecedor Planilha": pay.at[best_match, "Fornecedor"] if "Fornecedor" in pay.columns else "",
                "Data Pagamento Planilha": pay.at[best_match, "Data de Pagamento"].strftime("%d/%m/%Y"),
                "Valor Planilha (R$)": float(pay.at[best_match, valor_col]),
                "Conta Bancária": pay.at[best_match, "Conta Bancária"],
                "Status": status,
            })
        else:
            unmatched_rows.append({
                "Data Extrato": stmt_row["Data"].strftime("%d/%m/%Y"),
                "Descrição Extrato": stmt_row["Descricao"],
                "Documento": stmt_row["Documento"],
                "Débito (R$)": stmt_row["Debito"],
                "Status": "Não encontrado na planilha",
            })

    # Registros da planilha não encontrados no extrato
    orphan_rows = []
    for idx in pay_idx:
        if not pay.at[idx, "_usado"]:
            orphan_rows.append({
                "Data Pagamento": pay.at[idx, "Data de Pagamento"].strftime("%d/%m/%Y") if pd.notna(pay.at[idx, "Data de Pagamento"]) else "",
                "Fornecedor": pay.at[idx, "Fornecedor"] if "Fornecedor" in pay.columns else "",
                "Descrição": pay.at[idx, "Descrição Pagamento"] if "Descrição Pagamento" in pay.columns else "",
                "Valor (R$)": float(pay.at[idx, valor_col]) if pd.notna(pay.at[idx, valor_col]) else "",
                "Conta Bancária": pay.at[idx, "Conta Bancária"],
                "Status": "Na planilha, não no extrato",
            })

    return (
        pd.DataFrame(matched_rows),
        pd.DataFrame(unmatched_rows),
        pd.DataFrame(orphan_rows),
    )


def to_excel_report(matched, unmatched, orphans) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        wb = writer.book

        # Formatos
        fmt_header = wb.add_format({
            "bold": True, "font_color": "white", "bg_color": "#2E4057",
            "border": 1, "align": "center", "valign": "vcenter", "font_size": 10
        })
        fmt_ok = wb.add_format({"bg_color": "#D6F5D6", "border": 1, "font_size": 9})
        fmt_warn = wb.add_format({"bg_color": "#FFF9C4", "border": 1, "font_size": 9})
        fmt_err = wb.add_format({"bg_color": "#FFDDD6", "border": 1, "font_size": 9})
        fmt_orphan = wb.add_format({"bg_color": "#E3F0FF", "border": 1, "font_size": 9})
        fmt_money = wb.add_format({"num_format": '#,##0.00', "border": 1, "font_size": 9})
        fmt_money_ok = wb.add_format({"num_format": '#,##0.00', "bg_color": "#D6F5D6", "border": 1, "font_size": 9})
        fmt_money_warn = wb.add_format({"num_format": '#,##0.00', "bg_color": "#FFF9C4", "border": 1, "font_size": 9})
        fmt_money_err = wb.add_format({"num_format": '#,##0.00', "bg_color": "#FFDDD6", "border": 1, "font_size": 9})

        def write_sheet(df, sheet_name, row_fmt, money_fmt, money_cols=None):
            if df.empty:
                ws = wb.add_worksheet(sheet_name)
                ws.write(0, 0, "Sem registros.")
                return
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            ws = writer.sheets[sheet_name]
            for col_num, col_name in enumerate(df.columns):
                ws.write(0, col_num, col_name, fmt_header)
                ws.set_column(col_num, col_num, max(len(col_name) + 4, 18))
            for row_num in range(1, len(df) + 1):
                for col_num, col_name in enumerate(df.columns):
                    val = df.iloc[row_num - 1, col_num]
                    if money_cols and col_name in money_cols and isinstance(val, (int, float)):
                        ws.write(row_num, col_num, val, money_fmt)
                    else:
                        ws.write(row_num, col_num, str(val) if pd.notna(val) else "", row_fmt)

        money_matched = ["Débito (R$)", "Valor Planilha (R$)"]
        money_orphan = ["Valor (R$)"]

        # Aba conciliados
        if not matched.empty:
            matched.to_excel(writer, sheet_name="Conciliados", index=False)
            ws = writer.sheets["Conciliados"]
            for col_num, col_name in enumerate(matched.columns):
                ws.write(0, col_num, col_name, fmt_header)
                ws.set_column(col_num, col_num, max(len(col_name) + 4, 18))
            for row_num in range(1, len(matched) + 1):
                status = str(matched.iloc[row_num - 1].get("Status", ""))
                rfmt = fmt_ok if "Conciliado" in status else fmt_warn
                mfmt = fmt_money_ok if "Conciliado" in status else fmt_money_warn
                for col_num, col_name in enumerate(matched.columns):
                    val = matched.iloc[row_num - 1, col_num]
                    if col_name in money_matched and isinstance(val, (int, float)):
                        ws.write(row_num, col_num, val, mfmt)
                    else:
                        ws.write(row_num, col_num, str(val) if pd.notna(val) else "", rfmt)

        write_sheet(unmatched, "Extrato sem Planilha", fmt_err, fmt_money_err, ["Débito (R$)"])
        write_sheet(orphans, "Planilha sem Extrato", fmt_orphan, fmt_money, money_orphan)

    output.seek(0)
    return output.read()


# ──────────────────────────────────────────────────────────────
# Interface
# ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Conciliação Bancária", layout="wide")

st.markdown("""
<style>
    /* Oculta elementos padrões do Streamlit para cara de sistema web nativo */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Header Estilo Landing Page Profissional */
    .main-header {
        background: linear-gradient(145deg, rgba(59, 130, 246, 0.05) 0%, rgba(16, 185, 129, 0.05) 100%);
        border: 1px solid rgba(128, 128, 128, 0.15);
        border-radius: 16px;
        padding: 2.5rem 2rem;
        margin-bottom: 2.5rem;
        position: relative;
    }
    .creator-badge {
        display: inline-flex;
        align-items: center;
        background-color: var(--background-color);
        border: 1px solid rgba(128, 128, 128, 0.2);
        padding: 0.3rem 0.8rem;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--text-color);
        margin-bottom: 1.2rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    .header-highlight {
        background: linear-gradient(90deg, #3b82f6, #10b981);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .main-header h1 { margin: 0; font-size: 2.6rem; font-weight: 800; letter-spacing: -0.02em; color: var(--text-color); line-height: 1.2; }
    .main-header p { margin: 0.5rem 0 0; color: gray; font-size: 1.15rem; max-width: 650px; }
    
    /* Cards de Métrica (Clean & Flat) */
    .metric-container {
        display: flex;
        flex-direction: column;
        background-color: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 10px; padding: 1.25rem;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        transition: all 0.2s ease;
        margin-bottom: 1rem;
    }
    .metric-container:hover {
        border-color: rgba(128, 128, 128, 0.5);
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }
    .metric-title {
        font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
        color: gray; font-weight: 600; margin-bottom: 0.5rem;
        display: flex; align-items: center; gap: 0.4rem;
    }
    .metric-value { font-size: 2rem; font-weight: 700; color: var(--text-color); line-height: 1; }

    /* Indicadores de Cor (Dots) */
    .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot-green { background-color: #10b981; }
    .dot-yellow { background-color: #f59e0b; }
    .dot-red { background-color: #ef4444; }
    .dot-blue { background-color: #3b82f6; }

    /* Badges de Etapa (Minimalistas/Pill) */
    .step-container { display: flex; align-items: center; margin: 1.5rem 0 1rem 0; }
    .step-badge {
        background-color: var(--text-color); color: var(--background-color);
        font-size: 0.75rem; font-weight: 700; padding: 0.2rem 0.6rem;
        border-radius: 999px; margin-right: 0.8rem; letter-spacing: 0.05em;
    }
    .step-text { font-size: 1.1rem; font-weight: 600; color: var(--text-color); }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
  <div class="creator-badge">Criado por Izabelli Soriano</div>
  <h1>Conciliação <span class="header-highlight">Bancária</span></h1>
  <p>Sincronize extratos bancários com contas a pagar de forma automatizada, rápida e segura.</p>
</div>
""", unsafe_allow_html=True)

# ── Estado da sessão ──────────────────────────────────────────
if "payments_df" not in st.session_state:
    st.session_state.payments_df = None
if "bank_stmt_df" not in st.session_state:
    st.session_state.bank_stmt_df = None
if "results" not in st.session_state:
    st.session_state.results = None

def clear_results():
    """Limpa os resultados da tela caso algum parâmetro seja alterado pelo usuário."""
    st.session_state.results = None

# ──────────────────────────────────────────────────────────────
# PASSO 1 — Planilha de Pagamentos
# ──────────────────────────────────────────────────────────────
st.markdown("""
<div class="step-container">
    <div class="step-badge">PASSO 1</div>
    <div class="step-text">Carregar a planilha de pagamentos (.xlsx)</div>
</div>
""", unsafe_allow_html=True)

col_left, col_right = st.columns([2, 1])
with col_left:
    xlsx_file = st.file_uploader("Planilha de pagamentos (.xlsx)", type=["xlsx", "xls"], key="xlsx_up", on_change=clear_results,
                                  label_visibility="collapsed")

if xlsx_file:
    try:
        st.session_state.payments_df = load_payments_xlsx(xlsx_file.getvalue())
        st.success(f"Planilha carregada — {len(st.session_state.payments_df):,} registros", icon=":material/check_circle:")
    except Exception as e:
        st.error(f"Erro ao ler a planilha: {e}")

payments_df = st.session_state.payments_df

st.divider()

# ──────────────────────────────────────────────────────────────
# PASSO 2 — Selecionar banco / contas
# ──────────────────────────────────────────────────────────────
st.markdown("""
<div class="step-container">
    <div class="step-badge">PASSO 2</div>
    <div class="step-text">Configurar banco e contas alvo</div>
</div>
""", unsafe_allow_html=True)

if payments_df is None:
    st.info("Carregue a planilha de pagamentos primeiro para ver as contas disponíveis.")
    st.stop()

all_contas = get_banks_from_xlsx(payments_df)
bank_names = sorted(set(detect_bank_from_conta(c) or "Outro" for c in all_contas))

col1, col2 = st.columns([1, 2])
with col1:
    selected_bank = st.selectbox(
        "Banco do extrato",
        options=bank_names,
        help="Selecione o banco correspondente ao arquivo CSV que você vai carregar",
        on_change=clear_results
    )

# Filtra contas do banco selecionado
contas_do_banco = [c for c in all_contas if (detect_bank_from_conta(c) or "Outro") == selected_bank]

with col2:
    selected_contas = st.multiselect(
        "Contas bancárias (da planilha)",
        options=contas_do_banco,
        default=contas_do_banco,
        help="Selecione quais contas desta planilha correspondem ao extrato carregado",
        on_change=clear_results
    )

if not selected_contas:
    st.warning("Selecione pelo menos uma conta bancária.")
    st.stop()

# Resumo do filtro
n_filtered = len(payments_df[payments_df["Conta Bancária"].isin(selected_contas)])
st.info(f"**{n_filtered:,}** registros encontrados para as contas selecionadas na planilha.", icon=":material/analytics:")

st.divider()

# ──────────────────────────────────────────────────────────────
# PASSO 3 — Extrato bancário
# ──────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="step-container">
    <div class="step-badge">PASSO 3</div>
    <div class="step-text">Processar extrato do {selected_bank} (.csv)</div>
</div>
""", unsafe_allow_html=True)

csv_file = st.file_uploader(f"Extrato bancário do {selected_bank} (.csv)", type=["csv", "txt"],
                              key="csv_up", label_visibility="collapsed", on_change=clear_results)

col_tol1, col_tol2 = st.columns(2)
with col_tol1:
    tolerance = st.number_input("Tolerância de valor (R$)", min_value=0.0, max_value=10.0,
                                 value=0.05, step=0.01, on_change=clear_results,
                                 help="Diferença máxima aceita entre o valor do extrato e da planilha")
with col_tol2:
    date_tol = st.number_input("Tolerância de data (dias)", min_value=0, max_value=5,
                                value=1, step=1, on_change=clear_results,
                                help="Diferença máxima de dias entre a data do extrato e da planilha")

if csv_file:
    raw = csv_file.getvalue()
    df_stmt = parse_bank_csv(raw, selected_bank)
    if df_stmt is None or df_stmt.empty:
        st.error("Não foi possível interpretar o arquivo CSV. Verifique se o formato está correto para o banco selecionado.")
        st.stop()
    st.session_state.bank_stmt_df = df_stmt

    n_deb = df_stmt["Debito"].notna().sum()
    n_cred = df_stmt["Credito"].notna().sum()
    st.success(f"Extrato carregado — {len(df_stmt):,} lançamentos ({n_deb} débitos, {n_cred} créditos)", icon=":material/check_circle:")

    with st.expander("Pré-visualizar extrato (10 primeiras linhas)", icon=":material/visibility:"):
        st.dataframe(df_stmt.head(10), use_container_width=True)

bank_stmt_df = st.session_state.bank_stmt_df

st.divider()

# ──────────────────────────────────────────────────────────────
# PASSO 4 — Conciliar
# ──────────────────────────────────────────────────────────────
st.markdown("""
<div class="step-container">
    <div class="step-badge">PASSO 4</div>
    <div class="step-text">Executar motor de conciliação</div>
</div>
""", unsafe_allow_html=True)

if bank_stmt_df is None:
    st.info("Carregue o extrato bancário para continuar.")
    st.stop()

if st.button("Conciliar agora", type="primary", use_container_width=True, icon=":material/sync:"):
    with st.spinner("Conciliando..."):
        matched, unmatched, orphans = reconcile(
            bank_stmt_df, payments_df, selected_contas,
            tolerance=tolerance, date_tolerance_days=date_tol
        )
        st.session_state.results = (matched, unmatched, orphans)
    st.success("Conciliação concluída!", icon=":material/task_alt:")

# ──────────────────────────────────────────────────────────────
# Resultados
# ──────────────────────────────────────────────────────────────
if st.session_state.results:
    matched, unmatched, orphans = st.session_state.results

    st.divider()
    st.subheader("Resultado")

    c1, c2, c3, c4 = st.columns(4)
    total_debits = bank_stmt_df["Debito"].notna().sum()
    n_exact = len(matched[matched["Status"] == "Conciliado"]) if not matched.empty else 0
    n_warn  = len(matched[matched["Status"].str.contains("próxima", na=False)]) if not matched.empty else 0
    n_err   = len(unmatched)
    n_orph  = len(orphans)

    with c1:
        st.markdown(f'<div class="metric-container"><div class="metric-title"><span class="status-dot dot-green"></span> Conciliados (Exato)</div><div class="metric-value">{n_exact}</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="metric-container"><div class="metric-title"><span class="status-dot dot-yellow"></span> Data Próxima (±{date_tol}d)</div><div class="metric-value">{n_warn}</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="metric-container"><div class="metric-title"><span class="status-dot dot-red"></span> Falta na Planilha</div><div class="metric-value">{n_err}</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div class="metric-container"><div class="metric-title"><span class="status-dot dot-blue"></span> Falta no Extrato</div><div class="metric-value">{n_orph}</div></div>', unsafe_allow_html=True)

    # Taxa de conciliação
    if total_debits > 0:
        taxa = ((n_exact + n_warn) / total_debits) * 100
        color = "green" if taxa >= 90 else ("orange" if taxa >= 70 else "red")
        st.markdown(f"**Taxa de conciliação:** :{color}[**{taxa:.1f}%**] dos débitos do extrato foram encontrados na planilha")

    # Tabs de resultados
    tab1, tab2, tab3 = st.tabs([
        f"Extrato sem Planilha ({n_err})",
        f"Planilha sem Extrato ({n_orph})",
        f"Conciliados ({n_exact + n_warn})",
    ])

    with tab1:
        if unmatched.empty:
            st.success("Todos os débitos do extrato foram encontrados na planilha!", icon=":material/celebration:")
        else:
            st.markdown(f"**{len(unmatched)} débitos do extrato não foram encontrados na planilha** para as contas selecionadas.")
            st.dataframe(
                unmatched.style.apply(lambda x: ["background-color: rgba(239, 68, 68, 0.15)"] * len(x), axis=1),
                use_container_width=True, height=400, hide_index=True
            )
            # Totalizador
            if "Débito (R$)" in unmatched.columns:
                total_nao_enc = unmatched["Débito (R$)"].sum()
                st.markdown(f"**Total não encontrado: R$ {total_nao_enc:,.2f}**")

    with tab2:
        if orphans.empty:
            st.success("Todos os registros da planilha foram encontrados no extrato!", icon=":material/celebration:")
        else:
            st.markdown(f"**{len(orphans)} registros da planilha não foram encontrados no extrato.**")
            st.dataframe(
                orphans.style.apply(lambda x: ["background-color: rgba(59, 130, 246, 0.15)"] * len(x), axis=1),
                use_container_width=True, height=400, hide_index=True
            )
            if "Valor (R$)" in orphans.columns:
                vals = pd.to_numeric(orphans["Valor (R$)"], errors="coerce")
                total_orph = vals.sum()
                st.markdown(f"**Total: R$ {total_orph:,.2f}**")

    with tab3:
        if matched.empty:
            st.info("Nenhum lançamento conciliado.")
        else:
            def color_matched(row):
                if "próxima" in str(row.get("Status", "")):
                    return ["background-color: rgba(245, 158, 11, 0.15)"] * len(row)
                return ["background-color: rgba(16, 185, 129, 0.15)"] * len(row)
            st.dataframe(
                matched.style.apply(color_matched, axis=1),
                use_container_width=True, height=400, hide_index=True
            )

    # Exportar
    st.divider()
    excel_bytes = to_excel_report(matched, unmatched, orphans)
    st.download_button(
        "Baixar relatório completo (.xlsx)",
        icon=":material/download:",
        data=excel_bytes,
        file_name="conciliacao_bancaria.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )
