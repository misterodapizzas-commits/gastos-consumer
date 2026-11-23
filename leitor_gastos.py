import os
import re
from io import BytesIO
import pandas as pd
import pypdfium2 as pdfium
import pytesseract
from PIL import Image
from pypdf import PdfReader
import streamlit as st
from supabase import Client, create_client

caminho_windows = r"C:\Arquivos de Programas\Tesseract-OCR\tesseract.exe"
if os.path.exists(caminho_windows):
    pytesseract.pytesseract.tesseract_cmd = caminho_windows

st.set_page_config(
    page_title="Consumer Expense Pro",
    page_icon="🧾",
    layout="wide",
)

CATEGORIAS_PADRAO = [
    "Insumos / Alimentos",
    "Bebidas",
    "Embalagens / Descartáveis",
    "Energia / Água / Internet",
    "Aluguel / Condomínio",
    "Manutenção / Limpeza",
    "Folha / Serviços",
    "Marketing / Taxas",
    "Outros",
]


def obter_credenciais_supabase() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    try:
        if "SUPABASE_URL" in st.secrets:
            url = str(st.secrets["SUPABASE_URL"])
        if "SUPABASE_KEY" in st.secrets:
            key = str(st.secrets["SUPABASE_KEY"])
    except Exception:
        pass
    return url.strip(), key.strip()


@st.cache_resource
def obter_cliente_supabase(url: str, chave: str) -> Client | None:
    if not url or not chave:
        return None
    return create_client(url.strip(), chave.strip())


def converter_moeda_para_float(texto_valor: str) -> float:
    if not texto_valor:
        return 0.0
    limpo = re.sub(r"[^\d,\.]", "", texto_valor).strip()
    if not limpo:
        return 0.0
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return round(float(limpo), 2)
    except ValueError:
        return 0.0


def formatar_moeda_br(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def extrair_texto_pdf_hibrido(arquivo_bytes: bytes) -> tuple[str, str]:
    reader = PdfReader(BytesIO(arquivo_bytes))
    textos_diretos = []
    for pagina in reader.pages:
        t = pagina.extract_text() or ""
        if t.strip():
            textos_diretos.append(t.strip())

    if textos_diretos:
        return "\n\n".join(textos_diretos), "PDF (texto digital)"

    pdf_doc = pdfium.PdfDocument(arquivo_bytes)
    textos_ocr = []
    for i in range(len(pdf_doc)):
        pagina = pdf_doc[i]
        bitmap = pagina.render(scale=2.0)
        pil_img = bitmap.to_pil()
        ocr_pag = pytesseract.image_to_string(pil_img, lang="por")
        if ocr_pag.strip():
            textos_ocr.append(f"--- Página {i + 1} ---\n{ocr_pag.strip()}")

    if textos_ocr:
        return "\n\n".join(textos_ocr), "PDF escaneado (OCR)"
    return "", "PDF sem texto detectado"


def extrair_texto_imagem(arquivo_bytes: bytes) -> tuple[str, str]:
    img = Image.open(BytesIO(arquivo_bytes)).convert("RGB")
    texto = pytesseract.image_to_string(img, lang="por")
    return texto.strip(), "Imagem (OCR)"


def detectar_valores(texto: str) -> list[float]:
    encontrados = re.findall(r"(?:R\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2}", texto)
    valores = [converter_moeda_para_float(v) for v in encontrados]
    valores_validos = [v for v in valores if v > 0]
    return sorted(set(valores_validos), reverse=True)


def detectar_datas(texto: str) -> list[str]:
    datas = re.findall(r"\b\d{2}/\d{2}/\d{2,4}\b", texto)
    ordenadas = []
    for d in datas:
        if d not in ordenadas:
            ordenadas.append(d)
    return ordenadas


def sugerir_fornecedor(texto: str, nome_arquivo: str) -> str:
    linhas = [l.strip() for l in texto.splitlines() if len(l.strip()) >= 3]
    for linha in linhas[:8]:
        if not re.search(r"(cnpj|cpf|ie:|insc|cupom|nf-e|nfc-e|danfe|data|hora|valor|total|venc)", linha.lower()):
            if not re.fullmatch(r"[\d\W]+", linha):
                return linha[:60]
    base = os.path.splitext(nome_arquivo or "Lancamento")[0]
    return re.sub(r"[_-]+", " ", base).strip()[:60]


if "fila_local" not in st.session_state:
    st.session_state.fila_local = []

url_padrao, key_padrao = obter_credenciais_supabase()

with st.sidebar:
    st.header("Banco na Nuvem (Supabase)")
    sb_url = st.text_input("Project URL", value=url_padrao, placeholder="https://xxxx.supabase.co")
    sb_key = st.text_input("Anon / Public Key", value=key_padrao, type="password", placeholder="eyJ...")

    cliente = obter_cliente_supabase(sb_url, sb_key)
    if cliente:
        st.success("Conectado ao banco na nuvem")
    else:
        st.info("Sem banco configurado ainda: funciona na fila local")

st.title("Consumer Expense Pro — Controle de Gastos")
st.caption("Escaneie fotos, PDFs digitais ou PDFs escaneados, revise os campos e prepare o lançamento para o Consumer.")

arquivo = st.file_uploader(
    "1. Envie uma foto (JPG/PNG) ou PDF (digital ou escaneado)",
    type=["png", "jpg", "jpeg", "pdf"],
)

texto_lido = ""
origem_leitura = ""
valores_sugeridos = []
datas_sugeridas = []
fornecedor_sugerido = ""

if arquivo is not None:
    dados_bytes = arquivo.getvalue()
    eh_pdf = arquivo.type == "application/pdf" or arquivo.name.lower().endswith(".pdf")

    with st.spinner("Lendo documento..."):
        if eh_pdf:
            texto_lido, origem_leitura = extrair_texto_pdf_hibrido(dados_bytes)
        else:
            st.image(dados_bytes, caption="Prévia do documento enviado", width=420)
            texto_lido, origem_leitura = extrair_texto_imagem(dados_bytes)

    valores_sugeridos = detectar_valores(texto_lido)
    datas_sugeridas = detectar_datas(texto_lido)
    fornecedor_sugerido = sugerir_fornecedor(texto_lido, arquivo.name)

    st.success(f"Leitura concluída via: {origem_leitura}")
    if valores_sugeridos:
        st.write("Valores encontrados:", ", ".join(formatar_moeda_br(v) for v in valores_sugeridos[:8]))
    if datas_sugeridas:
        st.write("Datas encontradas:", ", ".join(datas_sugeridas[:8]))

    with st.expander("Ver texto completo extraído pelo OCR/PDF"):
        st.text_area("Texto bruto", value=texto_lido, height=180, label_visibility="collapsed")

st.subheader("2. Conferência para lançar no Consumer")
valor_inicial = valores_sugeridos[0] if valores_sugeridos else 0.0
data_inicial = datas_sugeridas[-1] if datas_sugeridas else ""

with st.form("form_despesa", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns([2.2, 1.8, 1.2, 1.2])
    with c1:
        fornecedor_input = st.text_input("Fornecedor", value=fornecedor_sugerido)
    with c2:
        categoria_input = st.selectbox("Categoria (Contas a Pagar / DRE)", CATEGORIAS_PADRAO)
    with c3:
        valor_input = st.number_input("Valor (R$)", min_value=0.0, value=float(valor_inicial), step=1.0, format="%.2f")
    with c4:
        vencimento_input = st.text_input("Vencimento", value=data_inicial, placeholder="DD/MM/AAAA")

    obs_input = st.text_input("Observação / Forma de pagamento / Nº documento", placeholder="Ex.: Boleto, PIX, NF-e 1234")
    salvar_btn = st.form_submit_button("Salvar despesa", type="primary")

if salvar_btn:
    registro = {
        "arquivo_nome": arquivo.name if arquivo else "Lancamento Manual",
        "fornecedor": fornecedor_input.strip() or "Fornecedor não informado",
        "categoria": categoria_input,
        "valor_numerico": float(valor_input),
        "vencimento": vencimento_input.strip(),
        "status": "Pendente",
        "observacao": obs_input.strip(),
    }

    if cliente:
        try:
            cliente.table("despesas_consumer").insert(registro).execute()
            st.toast("Salvo na nuvem com sucesso!")
        except Exception as erro:
            st.error(f"Erro ao salvar no Supabase: {erro}")
            st.session_state.fila_local.append(registro)
    else:
        st.session_state.fila_local.append(registro)
        st.toast("Adicionado à fila local!")


def carregar_registros() -> list[dict]:
    if cliente:
        try:
            resp = cliente.table("despesas_consumer").select("*").order("criado_em", desc=True).execute()
            return resp.data or []
        except Exception as erro:
            st.warning(f"Não foi possível consultar a nuvem agora ({erro}). Exibindo fila local.")
    return list(reversed(st.session_state.fila_local))


registros = carregar_registros()

st.divider()
st.subheader("3. Painel de Lançamentos para o Consumer")

if registros:
    df = pd.DataFrame(registros)

    f1, f2, f3 = st.columns(3)
    with f1:
        filtro_status = st.selectbox("Filtrar por status", ["Todos", "Pendente", "Lançado no Consumer"])
    with f2:
        categorias_presentes = ["Todas"] + sorted(df["categoria"].dropna().unique().tolist())
        filtro_cat = st.selectbox("Filtrar por categoria", categorias_presentes)
    with f3:
        busca_fornecedor = st.text_input("Buscar fornecedor", placeholder="Digite para filtrar")

    df_filtrado = df.copy()
    if filtro_status != "Todos":
        df_filtrado = df_filtrado[df_filtrado["status"] == filtro_status]
    if filtro_cat != "Todas":
        df_filtrado = df_filtrado[df_filtrado["categoria"] == filtro_cat]
    if busca_fornecedor.strip():
        df_filtrado = df_filtrado[
            df_filtrado["fornecedor"].fillna("").str.contains(busca_fornecedor.strip(), case=False, na=False)
        ]

    total_filtrado = pd.to_numeric(df_filtrado["valor_numerico"], errors="coerce").fillna(0.0).sum()
    pendentes_qtd = int((df["status"] == "Pendente").sum()) if "status" in df.columns else len(df)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total filtrado", formatar_moeda_br(float(total_filtrado)))
    m2.metric("Pendentes de lançar no Consumer", pendentes_qtd)
    m3.metric("Quantidade de registros", len(df_filtrado))

    colunas_exibir = [c for c in ["id", "fornecedor", "categoria", "valor_numerico", "vencimento", "status", "observacao", "arquivo_nome"] if c in df_filtrado.columns]
    df_editavel = st.data_editor(
        df_filtrado[colunas_exibir],
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": None,
            "valor_numerico": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
            "status": st.column_config.SelectboxColumn("Status", options=["Pendente", "Lançado no Consumer"]),
        },
    )

    if cliente and "id" in df_editavel.columns:
        if st.button("Atualizar alterações na nuvem"):
            try:
                for _, linha in df_editavel.iterrows():
                    cliente.table("despesas_consumer").update({
                        "fornecedor": linha["fornecedor"],
                        "categoria": linha["categoria"],
                        "valor_numerico": float(linha["valor_numerico"] or 0),
                        "vencimento": linha["vencimento"],
                        "status": linha["status"],
                        "observacao": linha["observacao"],
                    }).eq("id", linha["id"]).execute()
                st.success("Alterações sincronizadas na nuvem!")
                st.rerun()
            except Exception as erro:
                st.error(f"Erro ao atualizar na nuvem: {erro}")

    df_export = df_editavel.drop(columns=["id"], errors="ignore")
    csv_bytes = df_export.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    st.download_button(
        label="Baixar CSV (separado por ponto e vírgula para Excel)",
        data=csv_bytes,
        file_name="lancamentos_consumer.csv",
        mime="text/csv",
    )
else:
    st.info("Nenhum gasto lançado ainda. Envie uma foto ou PDF acima para começar.")