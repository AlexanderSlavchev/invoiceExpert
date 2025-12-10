import streamlit as st
import pandas as pd
import google.generativeai as genai
import time
import json
import re
import io
import zipfile

# --- НАСТРОЙКИ ---
API_KEY = st.secrets["GOOGLE_API_KEY"]

# --- КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="Invoice Expert", page_icon="📄", layout="wide")

# Речник за транслитерация
TRANSLIT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
    'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
    'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sht', 'ъ': 'a', 'ь': 'y',
    'ю': 'yu', 'я': 'ya',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ж': 'Zh',
    'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N',
    'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F',
    'Х': 'H', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sht', 'Ъ': 'A', 'Ь': 'Y',
    'Ю': 'Yu', 'Я': 'Ya'
}

def clean_json_string(json_str):
    return json_str.replace("```json", "").replace("```", "").strip()

def transliterate_text(text):
    if not text or not isinstance(text, str): return str(text) if text else "Unknown"
    result = ""
    for char in text:
        result += TRANSLIT_MAP.get(char, char)
    result = re.sub(r'[\\/*?:"<>|]', "", result)
    return result.strip()

def find_po_fallback(text):
    if not text: return ""
    pattern = r"(?:PO|P\.O\.|CP|Purchase Order|Order)[^0-9\n]*?(\d+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match: return match.group(1)
    if str(text).replace(" ", "").isdigit(): return text
    return ""

def process_single_file(bytes_data):
    try:
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        
        prompt = """
        Разгледай този документ. Извлечи данните в чист JSON формат.
        Полета:
        1. VendorName: Име на фирмата.
        2. InvoiceNumber: Номер на фактурата.
        3. Currency: Валута (BGN, EUR, USD).
        4. TotalAmount: Сума за плащане с ДДС (число).
        5. InvoiceDate: Дата на издаване (DD.MM.YYYY).
        6. PONumber: Номер на поръчка (PO / CP Number).
        7. full_text: Върни целия суров текст от фактурата.
        Върни САМО JSON обекта.
        """
        
        document_part = {"mime_type": "application/pdf", "data": bytes_data}
        response = model.generate_content([document_part, prompt])
        return response.text

    except Exception as e:
        if "429" in str(e):
            time.sleep(10)
            return process_single_file(bytes_data)
        raise e

# --- UI (ИНТЕРФЕЙС) ---
st.title("🤖 AI Екстрактор + Преименуване")
st.markdown("Качи PDF файловете, избери начален номер и аз ще ти върна Excel таблица + преименувани файлове.")

# 1. Инициализиране на 'паметта' (Session State)
if 'processed_data' not in st.session_state:
    st.session_state.processed_data = None
if 'zip_archive' not in st.session_state:
    st.session_state.zip_archive = None

# Секция за настройки
col1, col2 = st.columns(2)
with col1:
    uploaded_files = st.file_uploader("1. Избери PDF файлове", type="pdf", accept_multiple_files=True)
with col2:
    start_number = st.number_input("2. Начален номер за файловете", min_value=1, value=1023, step=1)

if uploaded_files:
    # Проверка дали сме натиснали бутона
    if st.button("🚀 ЗАПОЧНИ ОБРАБОТКА", type="primary"):
        if not API_KEY or "СЛОЖИ_ТВОЯ" in API_KEY:
            st.error("Липсва API Key в кода!")
            st.stop()

        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_data = []
        renamed_files_data = []
        current_seq_number = start_number
        
        for i, file in enumerate(uploaded_files):
            status_text.text(f"Обработвам: {file.name}...")
            file_bytes = file.getvalue()

            try:
                raw_response = process_single_file(file_bytes)
                json_str = clean_json_string(raw_response)
                data = json.loads(json_str)

                po_number = data.get("PONumber", "")
                full_text = data.get("full_text", "")
                if not po_number: po_number = find_po_fallback(full_text)
                if po_number and not str(po_number).isdigit():
                     clean_try = find_po_fallback("PO " + str(po_number))
                     if clean_try: po_number = clean_try

                raw_vendor = data.get("VendorName", "")
                latin_vendor = transliterate_text(raw_vendor)

                new_filename = f"{current_seq_number}_{latin_vendor}.pdf"
                renamed_files_data.append({"name": new_filename, "data": file_bytes})

                row = {
                    "Старо име": file.name,
                    "Ново име": new_filename,
                    "Доставчик": latin_vendor,
                    "Фактура №": data.get("InvoiceNumber", ""),
                    "Дата": data.get("InvoiceDate", ""),
                    "Валута": data.get("Currency", ""),
                    "Сума": data.get("TotalAmount", 0),
                    "PO Номер": po_number
                }
                all_data.append(row)
                current_seq_number += 1
                
            except Exception as e:
                st.error(f"Грешка с {file.name}: {e}")
                all_data.append({"Старо име": file.name, "Доставчик": "ГРЕШКА"})
            
            progress_bar.progress((i + 1) / len(uploaded_files))
            time.sleep(0.3)

        status_text.success("Готово! Данните са извлечени.")
        
        # 2. ЗАПАЗВАНЕ В ПАМЕТТА (Session State)
        # Това е важното! Тук казваме на Streamlit: "Запомни тези данни завинаги!"
        st.session_state.processed_data = pd.DataFrame(all_data)
        
        # Генерираме ZIP веднага и го пазим като bytes
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for item in renamed_files_data:
                zf.writestr(item["name"], item["data"])
        st.session_state.zip_archive = zip_buffer.getvalue()

# --- 3. ПОКАЗВАНЕ НА РЕЗУЛТАТИТЕ ---
# Този блок е ИЗВЪН бутона. Той се изпълнява винаги, когато имаме запазени данни.
if st.session_state.processed_data is not None:
    st.divider()
    st.subheader("📊 Резултати")
    
    # Показваме таблицата
    st.dataframe(st.session_state.processed_data)

    col_dl_1, col_dl_2 = st.columns(2)

    # Бутон за Excel
    buffer_excel = io.BytesIO()
    with pd.ExcelWriter(buffer_excel, engine='openpyxl') as writer:
        st.session_state.processed_data.to_excel(writer, index=False, sheet_name='Sheet1')
    
    with col_dl_1:
        st.download_button(
            label="📥 Изтегли EXCEL таблица",
            data=buffer_excel.getvalue(),
            file_name="invoice_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # Бутон за ZIP
    with col_dl_2:
        if st.session_state.zip_archive:
            st.download_button(
                label="📦 Изтегли ПРЕИМЕНУВАНИТЕ файлове (ZIP)",
                data=st.session_state.zip_archive,
                file_name="renamed_invoices.zip",
                mime="application/zip"
            )