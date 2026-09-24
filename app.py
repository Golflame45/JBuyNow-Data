import streamlit as st
import pandas as pd
import plotly.express as px
import io
import os
import re
from datetime import datetime

# Google Drive API Libraries
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

st.set_page_config(page_title="Omnichannel E-commerce Dashboard", layout="wide")

# ================= Google Drive Folder IDs =================
INBOX_FOLDER_ID = "1UYXJ1cyBHs1LHndB_Os5Rl8men7_7PQ5"
MASTER_FOLDER_ID = "1RNsLYo4TE_jEQOnMgVuKOW8RmFHiAjQf"
ARCHIVE_FOLDER_ID = "1MrQq5V3CuRwrO1pp9ZDzdYdVMdizOPP7"

# ================= 0. Authentication =================
def check_password():
    def password_entered():
        if st.session_state["password"] == "1234":
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("<h2 style='text-align: center;'>🔒 กรุณาใส่รหัสผ่านเพื่อเข้าสู่ระบบ</h2>", unsafe_allow_html=True)
        st.text_input("รหัสผ่าน", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.markdown("<h2 style='text-align: center;'>🔒 กรุณาใส่รหัสผ่านเพื่อเข้าสู่ระบบ</h2>", unsafe_allow_html=True)
        st.text_input("รหัสผ่าน", type="password", on_change=password_entered, key="password")
        st.error("❌ รหัสผ่านไม่ถูกต้องครับ")
        return False
    else:
        return True

if check_password():

    # ================= 1. Google Drive Helper Functions =================
    def get_drive_service():
        if not GOOGLE_API_AVAILABLE:
            return None
        creds = None
        import json

        # Check Streamlit Cloud Secrets
        if "gcp_service_account" in st.secrets:
            raw_val = st.secrets["gcp_service_account"]
            if isinstance(raw_val, str):
                creds_dict = json.loads(raw_val)
            else:
                creds_dict = dict(raw_val)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
        elif "gcp_json" in st.secrets:
            creds_dict = json.loads(st.secrets["gcp_json"])
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
        # Check local JSON file if running locally
        elif os.path.exists("service_account.json"):
            creds = service_account.Credentials.from_service_account_file(
                "service_account.json", scopes=['https://www.googleapis.com/auth/drive']
            )
        if creds:
            return build('drive', 'v3', credentials=creds)
        return None

    def list_files_in_folder(service, folder_id):
        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
        return results.get('files', [])

    def download_file_bytes(service, file_id):
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return fh

    def upload_file_bytes(service, folder_id, file_name, file_bytes, mime_type='text/csv'):
        # Check if file already exists in folder
        existing = service.files().list(
            q=f"'{folder_id}' in parents and name = '{file_name}' and trashed = false",
            fields="files(id)"
        ).execute().get('files', [])
        
        media = MediaIoBaseUpload(file_bytes, mimetype=mime_type, resumable=True)
        if existing:
            file_id = existing[0]['id']
            updated = service.files().update(fileId=file_id, media_body=media).execute()
            return updated['id']
        else:
            file_metadata = {'name': file_name, 'parents': [folder_id]}
            created = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            return created['id']

    def move_file_to_archive(service, file_id, current_parent_id, archive_parent_id):
        service.files().update(
            fileId=file_id,
            addParents=archive_parent_id,
            removeParents=current_parent_id,
            fields='id, parents'
        ).execute()

    # ================= 2. Multi-Channel Data Normalization =================
    def parse_raw_sales_file(file_bytes, file_name, folder_name=""):
        # Determine format
        if file_name.endswith('.csv'):
            df = pd.read_csv(file_bytes)
        else:
            df = pd.read_excel(file_bytes)

        # Detect Platform
        name_check = (file_name + " " + folder_name).lower()
        if "lazada" in name_check or "laz" in name_check:
            platform = "Lazada"
        else:
            platform = "Shopee"

        # Detect Shop Name from folder name or file name
        shop_name = "General"
        if folder_name:
            shop_name = folder_name.replace("Lazada_", "").replace("Shopee_", "").replace("Lazada", "").replace("Shopee", "").strip(" _-")
            if not shop_name:
                shop_name = folder_name
        elif "_" in file_name:
            parts = file_name.split("_")
            if len(parts) >= 2:
                shop_name = parts[1]

        # Extract Date
        date_match = re.search(r'202\d{5}', file_name)
        file_date_str = None
        if date_match:
            try:
                d = datetime.strptime(date_match.group(0), '%Y%m%d')
                file_date_str = d.strftime('%Y-%m-%d')
            except:
                pass

        # Mapping for Shopee & Lazada columns
        col_mappings = {
            'Revenue': ['ยอดขาย (ที่มีการสั่งซื้อทั้งหมด) (THB)', 'ยอดขาย (THB)', 'LAZ Revenue', 'Revenue', 'ยอดขาย'],
            'Visitors': ['ผู้เข้าชมสินค้า', 'การเข้าชมสินค้า', 'SKU_Visitors', 'Visitors'],
            'Buyers': ['ผู้ซื้อ (ที่มีการสั่งซื้อทั้งหมด)', 'ผู้ซื้อ', 'Buyer', 'Buyers'],
            'Units_Sold': ['จำนวนที่ขายได้ (ที่มีการสั่งซื้อทั้งหมด)', 'จำนวนที่ขายได้', 'UnitsSold', 'Units Sold', 'Units_Sold'],
            'A2C': ['จำนวนที่ขายได้ (เพิ่มสินค้าในรถเข็น)', 'A2C Units', 'Add2CartUnits', 'A2C'],
            'Orders': ['ทั้งหมด', 'Orders', 'Order No.'],
            'SKU': ['SKU', 'Seller SKU', 'SKU Code', 'รหัสสินค้า'],
            'Parent_SKU': ['Parent SKU', 'Product Group', 'กลุ่มสินค้า'],
            'Product': ['ผลิตภัณฑ์', 'Item Name', 'Product Name', 'Product Group', 'ชื่อสินค้า'],
            'Date': ['Date', 'OrderDate', 'Posting Date', 'วันที่']
        }

        clean_df = pd.DataFrame()
        for standard_col, candidate_cols in col_mappings.items():
            matched = False
            for cand in candidate_cols:
                if cand in df.columns:
                    clean_df[standard_col] = df[cand]
                    matched = True
                    break
            if not matched:
                clean_df[standard_col] = None

        clean_df['Platform'] = platform
        clean_df['Shop_Name'] = shop_name if shop_name else "Main Shop"

        # Date handling
        if file_date_str and clean_df['Date'].isna().all():
            clean_df['Date'] = file_date_str
        elif clean_df['Date'].isna().all():
            clean_df['Date'] = datetime.today().strftime('%Y-%m-%d')

        # Clean numeric columns
        numeric_cols = ['Revenue', 'Visitors', 'Buyers', 'Units_Sold', 'A2C', 'Orders']
        for nc in numeric_cols:
            clean_df[nc] = (
                clean_df[nc].astype(str)
                .str.replace(',', '')
                .str.replace('-', '0')
                .str.replace('nan', '0')
                .str.replace('None', '0')
            )
            clean_df[nc] = pd.to_numeric(clean_df[nc], errors='coerce').fillna(0)

        clean_df['SKU'] = clean_df['SKU'].astype(str)
        clean_df['Parent_SKU'] = clean_df['Parent_SKU'].astype(str)
        clean_df['Product'] = clean_df['Product'].astype(str)
        clean_df['DateObj'] = pd.to_datetime(clean_df['Date'], format='mixed', dayfirst=True, errors='coerce')
        clean_df = clean_df.dropna(subset=['DateObj'])
        clean_df['Date'] = clean_df['DateObj'].dt.strftime('%Y-%m-%d')

        return clean_df

    # ================= 3. Automated Ingestion Pipeline =================
    def sync_google_drive_pipeline(service):
        if not service:
            return "ไม่สามารถเชื่อมต่อ Google Drive API ได้ (กรุณาเช็ค Secrets)", 0

        # Step 1: Check existing Master file in 02_Master_Data
        master_files = list_files_in_folder(service, MASTER_FOLDER_ID)
        master_file_item = next((f for f in master_files if f['name'] == 'Master_Sales_Full.csv'), None)
        
        if master_file_item:
            fh = download_file_bytes(service, master_file_item['id'])
            master_df = pd.read_csv(fh)
        else:
            master_df = pd.DataFrame()

        # Step 2: Scan 01_Drop_Inbox (both direct files and subfolders)
        inbox_items = list_files_in_folder(service, INBOX_FOLDER_ID)
        new_dfs = []
        files_processed_count = 0

        for item in inbox_items:
            # Case A: Subfolder (e.g. Lazada_S.ELECTRIC, Shopee_Shop1)
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                sub_files = list_files_in_folder(service, item['id'])
                for sf in sub_files:
                    if sf['name'].endswith(('.xlsx', '.xls', '.csv')):
                        fb = download_file_bytes(service, sf['id'])
                        df_parsed = parse_raw_sales_file(fb, sf['name'], folder_name=item['name'])
                        new_dfs.append(df_parsed)
                        move_file_to_archive(service, sf['id'], item['id'], ARCHIVE_FOLDER_ID)
                        files_processed_count += 1
            # Case B: Direct file in 01_Drop_Inbox
            elif item['name'].endswith(('.xlsx', '.xls', '.csv')):
                fb = download_file_bytes(service, item['id'])
                df_parsed = parse_raw_sales_file(fb, item['name'])
                new_dfs.append(df_parsed)
                move_file_to_archive(service, item['id'], INBOX_FOLDER_ID, ARCHIVE_FOLDER_ID)
                files_processed_count += 1

        # Step 3: Append, Deduplicate, and Save back to 02_Master_Data
        if new_dfs:
            combined_new = pd.concat(new_dfs, ignore_index=True)
            if not master_df.empty:
                full_df = pd.concat([master_df, combined_new], ignore_index=True)
            else:
                full_df = combined_new
            
            # Deduplicate by Platform, Shop_Name, Date, SKU
            full_df = full_df.drop_duplicates(subset=['Platform', 'Shop_Name', 'Date', 'SKU'], keep='last')
            
            # Save back to Google Drive
            csv_buf = io.BytesIO()
            full_df.to_csv(csv_buf, index=False, encoding='utf-8-sig')
            csv_buf.seek(0)
            upload_file_bytes(service, MASTER_FOLDER_ID, 'Master_Sales_Full.csv', csv_buf, mime_type='text/csv')
            master_df = full_df

        return "ซิงก์สำเร็จ", files_processed_count

    # ================= 4. Load Master & SKU Master Data =================
    @st.cache_data(ttl=600)
    def load_active_data():
        service = get_drive_service()
        master_df = pd.DataFrame()

        if service:
            master_files = list_files_in_folder(service, MASTER_FOLDER_ID)
            # Find Master Sales
            m_item = next((f for f in master_files if f['name'] == 'Master_Sales_Full.csv'), None)
            if m_item:
                fh = download_file_bytes(service, m_item['id'])
                master_df = pd.read_csv(fh)
            
            # Find SKU Master if exists in 02_Master_Data
            sku_item = next((f for f in master_files if 'sku' in f['name'].lower() and 'master' in f['name'].lower()), None)
            if sku_item and not master_df.empty:
                sku_bytes = download_file_bytes(service, sku_item['id'])
                if sku_item['name'].endswith('.csv'):
                    sku_master_df = pd.read_csv(sku_bytes)
                else:
                    sku_master_df = pd.read_excel(sku_bytes)
                
                # Normalize SKU column name in master
                sku_col = next((c for c in sku_master_df.columns if c.lower() in ['sku', 'seller sku', 'รหัสสินค้า']), None)
                if sku_col:
                    sku_master_df['SKU'] = sku_master_df[sku_col].astype(str)
                    master_df['SKU'] = master_df['SKU'].astype(str)
                    # Merge ERP Catalog
                    master_df = master_df.merge(sku_master_df, on='SKU', how='left', suffixes=('', '_ERP'))
        else:
            # Local fallback for offline testing
            if os.path.exists('Master_Shopee_Data.csv'):
                master_df = pd.read_csv('Master_Shopee_Data.csv')
                # Minimal shopee column adaptation for local fallback
                if 'ยอดขาย (ที่มีการสั่งซื้อทั้งหมด) (THB)' in master_df.columns:
                    master_df['Revenue'] = master_df['ยอดขาย (ที่มีการสั่งซื้อทั้งหมด) (THB)'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    master_df['Visitors'] = master_df['ผู้เข้าชมสินค้า'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    master_df['Buyers'] = master_df['ผู้ซื้อ (ที่มีการสั่งซื้อทั้งหมด)'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    master_df['Units_Sold'] = master_df['จำนวนที่ขายได้ (ที่มีการสั่งซื้อทั้งหมด)'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    master_df['A2C'] = master_df['จำนวนที่ขายได้ (เพิ่มสินค้าในรถเข็น)'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    master_df['Product'] = master_df['ผลิตภัณฑ์'].astype(str)
                    master_df['Parent_SKU'] = master_df['Parent SKU'].astype(str)
                    master_df['Platform'] = 'Shopee'
                    master_df['Shop_Name'] = 'Official Shop'

        if not master_df.empty:
            # Guarantee all numeric columns exist
            for c in ['Revenue', 'Visitors', 'Buyers', 'Units_Sold', 'A2C', 'Orders']:
                if c not in master_df.columns:
                    if c == 'Orders' and 'ทั้งหมด' in master_df.columns:
                        master_df['Orders'] = master_df['ทั้งหมด'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    else:
                        master_df[c] = 0.0
                master_df[c] = pd.to_numeric(master_df[c], errors='coerce').fillna(0.0)

            master_df['DateObj'] = pd.to_datetime(master_df['Date'], format='mixed', dayfirst=True, errors='coerce')
            master_df = master_df.dropna(subset=['DateObj']).copy()
            master_df['Year'] = master_df['DateObj'].dt.year.astype(str)
            master_df['Month'] = master_df['DateObj'].dt.strftime('%Y-%m')
            master_df['Day'] = master_df['DateObj'].dt.strftime('%d/%m/%Y')
            master_df['SKU'] = master_df['SKU'].astype(str)
            master_df['Parent_SKU'] = master_df.get('Parent_SKU', master_df.get('Product Group', 'General')).astype(str)
            master_df['Product'] = master_df.get('Product', master_df.get('Item Name', 'General')).astype(str)
            master_df['Platform'] = master_df.get('Platform', pd.Series(['Shopee'] * len(master_df))).fillna('Shopee').astype(str)
            master_df['Shop_Name'] = master_df.get('Shop_Name', pd.Series(['Main Shop'] * len(master_df))).fillna('Main Shop').astype(str)

        return master_df

    # ================= 5. Sidebar Sync & Administration =================
    with st.sidebar:
        st.header("⚙️ ระบบท่อข้อมูลอัตโนมัติ")
        st.write("**Google Drive Auto-Sync**")
        if st.button("🔄 ซิงก์ข้อมูลจาก Google Drive"):
            with st.spinner("กำลังตรวจสอบไฟล์ใหม่ใน 01_Drop_Inbox..."):
                srv = get_drive_service()
                status_msg, count = sync_google_drive_pipeline(srv)
                st.cache_data.clear()
                if count > 0:
                    st.success(f"นำเข้าข้อมูลใหม่สำเร็จ {count} ไฟล์!")
                else:
                    st.info("ไม่มีไฟล์ใหม่ในห้อง 01_Drop_Inbox (ข้อมูลเป็นปัจจุบันแล้ว)")
                st.rerun()

    base_df = load_active_data()

    # ================= 6. UI Banner & Top Filters =================
    st.markdown("""
        <style>
        .pbi-bar {
            background: linear-gradient(90deg, #FF007F 0%, #0000FF 100%);
            color: white; padding: 12px 18px;
            font-size: 22px; font-weight: bold; text-align: left;
            border-radius: 6px; margin-bottom: 15px;
            display: flex; justify-content: space-between; align-items: center;
        }
        [data-testid="stMetricValue"] { font-size: 24px !important; }
        [data-testid="stMetric"] {
            border: 1px solid var(--secondary-background-color); 
            padding: 10px; border-radius: 6px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
        }
        </style>
        <div class="pbi-bar">
            <span>OMNICHANNEL SALES DASHBOARD</span>
            <span style="font-size: 14px; font-weight: normal; opacity: 0.9;">SHOPEE & LAZADA MULTI-SHOP</span>
        </div>
    """, unsafe_allow_html=True)

    # Empty State Handling (Day 0)
    if base_df.empty:
        st.info("👋 **ระบบพร้อมทำงาน 100% แล้วครับ!**\n\nขณะนี้ยังไม่มีข้อมูลในระบบ ให้ทีมงานนำไฟล์ยอดขายรายวันไปหยอดไว้ในห้อง **`01_Drop_Inbox`** บน Google Drive จากนั้นกดปุ่ม **'🔄 ซิงก์ข้อมูลจาก Google Drive'** ที่แถบเมนูด้านซ้ายเพื่อเริ่มการคำนวณวันแรกได้ทันทีครับ!")
        st.stop()

    # ================= 7. Global Filters =================
    col_p1, col_p2, col_p3, col_p4 = st.columns([1, 1.5, 1, 2])
    
    with col_p1:
        platforms = ['ทั้งหมด (All)'] + sorted(base_df['Platform'].unique().tolist())
        sel_platform = st.selectbox("ช่องทาง (Platform)", platforms)

    with col_p2:
        available_shops = base_df if sel_platform == 'ทั้งหมด (All)' else base_df[base_df['Platform'] == sel_platform]
        shop_list = sorted(available_shops['Shop_Name'].unique().tolist())
        sel_shops = st.multiselect("ร้านค้า (Shop)", shop_list, default=shop_list)

    with col_p3:
        all_years = sorted(base_df['Year'].unique())
        selected_years = st.multiselect("ปี (Year)", all_years, default=all_years)

    with col_p4:
        all_parents = sorted(list(set([str(s) for s in base_df['Parent_SKU'].unique() if str(s) not in ['-', 'nan', 'NaN']])))
        selected_parents = st.multiselect("กลุ่มสินค้า (Category)", all_parents)

    filtered_df = base_df.copy()
    if sel_platform != 'ทั้งหมด (All)':
        filtered_df = filtered_df[filtered_df['Platform'] == sel_platform]
    if sel_shops:
        filtered_df = filtered_df[filtered_df['Shop_Name'].isin(sel_shops)]
    if selected_years:
        filtered_df = filtered_df[filtered_df['Year'].isin(selected_years)]
    if selected_parents:
        filtered_df = filtered_df[filtered_df['Parent_SKU'].isin(selected_parents)]

    # ================= 8. Base Tables for Fixed Row Mapping =================
    monthly_base = filtered_df.groupby('Month').agg({'Revenue': 'sum'}).reset_index().sort_values('Month')
    # ================= 8. Base Tables for Fixed Row Mapping =================
    monthly_base = filtered_df.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index().sort_values('Month')
    prod_base = filtered_df.groupby('Product').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'}).reset_index().sort_values('Revenue', ascending=False)
    prod_base['Avg CR'] = (prod_base['Buyers'] / prod_base['Visitors'] * 100).fillna(0)
    prod_base['Avg Price'] = (prod_base['Revenue'] / prod_base['Units_Sold']).fillna(0)

    daily_base = filtered_df.groupby('Day').agg({'DateObj': 'first', 'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index().sort_values('DateObj')
    sku_base = filtered_df.groupby('SKU').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'}).reset_index().sort_values('Revenue', ascending=False)
    sku_base['Avg CR'] = (sku_base['Buyers'] / sku_base['Visitors'] * 100).fillna(0)
    sku_base['Avg Price'] = (sku_base['Revenue'] / sku_base['Units_Sold']).fillna(0)

    # Cross-Filtering Extraction
    def get_selected_rows(key):
        val = st.session_state.get(key)
        if not val: return []
        if isinstance(val, dict): return val.get('selection', {}).get('rows', [])
        if hasattr(val, 'selection') and hasattr(val.selection, 'rows'): return val.selection.rows
        try: return val['selection']['rows']
        except: return []

    def get_selected_items(key, id_col, fallback_df):
        sel_idx = get_selected_rows(key)
        if not sel_idx: return []
        stored_ids = st.session_state.get(f"{key}_rendered_ids", [])
        if stored_ids:
            return [stored_ids[i] for i in sel_idx if i < len(stored_ids)]
        elif id_col in fallback_df.columns:
            return [fallback_df.iloc[i][id_col] for i in sel_idx if i < len(fallback_df)]
        return []

    selected_months = get_selected_items('tb_month', 'Month', monthly_base)
    selected_prods = get_selected_items('tb_prod', 'Product', prod_base)
    selected_days = get_selected_items('tb_day', 'Day', daily_base)
    selected_skus = get_selected_items('tb_sku', 'SKU', sku_base)

    cross_df = filtered_df.copy()
    if selected_months: cross_df = cross_df[cross_df['Month'].isin(selected_months)]
    if selected_prods: cross_df = cross_df[cross_df['Product'].isin(selected_prods)]
    if selected_days: cross_df = cross_df[cross_df['Day'].isin(selected_days)]
    if selected_skus: cross_df = cross_df[cross_df['SKU'].isin(selected_skus)]

    # ================= 9. KPI Scorecards =================
    st.markdown("---")
    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6, kpi7 = st.columns(7)
    rev = cross_df['Revenue'].sum() if 'Revenue' in cross_df.columns else 0
    vis = cross_df['Visitors'].sum() if 'Visitors' in cross_df.columns else 0
    buy = cross_df['Buyers'].sum() if 'Buyers' in cross_df.columns else 0
    unit = cross_df['Units_Sold'].sum() if 'Units_Sold' in cross_df.columns else 0
    orders = cross_df['Orders'].sum() if 'Orders' in cross_df.columns else 0
    cr = (buy / vis) if vis > 0 else 0
    rev_per_buyer = (rev / buy) if buy > 0 else 0
    aov = (rev / orders) if orders > 0 else (rev / buy if buy > 0 else 0)

    kpi1.metric("Revenue (ยอดขาย)", f"฿{rev:,.0f}")
    kpi2.metric("SKU Visitors", f"{vis:,.0f}")
    kpi3.metric("SKU CR%", f"{cr*100:,.2f}%")
    kpi4.metric("Rev per Buyers", f"฿{rev_per_buyer:,.0f}")
    kpi5.metric("AOV", f"฿{aov:,.0f}")
    kpi6.metric("Buyers (ผู้ซื้อ)", f"{buy:,.0f}")
    kpi7.metric("Units Sold", f"{unit:,.0f}")

    st.markdown("---")

    # ================= 10. Display Tables =================
    # Section ที่ถูกคลิก จะแสดงข้อมูลเต็มไม่กลายเป็น 0
    # Section อื่นๆ ที่ไม่ได้ถูกคลิก แถวที่ไม่เกี่ยวข้องจะ "หายไปเลย" (ไม่เป็น 0)
    
    # 1. Order Month
    if selected_months:
        disp_monthly = monthly_base.copy()
    else:
        disp_monthly = cross_df.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index()
        disp_monthly = disp_monthly[disp_monthly['Revenue'] > 0].sort_values('Month')
    disp_monthly['% Rev'] = (disp_monthly['Revenue'] / disp_monthly['Revenue'].sum() * 100).fillna(0) if disp_monthly['Revenue'].sum() > 0 else 0
    st.session_state['tb_month_rendered_ids'] = disp_monthly['Month'].tolist()

    # 2. Product Group
    if selected_prods:
        disp_prod = prod_base.copy()
    else:
        disp_prod = cross_df.groupby('Product').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'}).reset_index()
        disp_prod = disp_prod[disp_prod['Revenue'] > 0]
        disp_prod['Avg CR'] = (disp_prod['Buyers'] / disp_prod['Visitors'] * 100).fillna(0)
        disp_prod['Avg Price'] = (disp_prod['Revenue'] / disp_prod['Units_Sold']).fillna(0)
        disp_prod = disp_prod.sort_values('Revenue', ascending=False)
    st.session_state['tb_prod_rendered_ids'] = disp_prod['Product'].tolist()

    # 3. Order Date
    if selected_days:
        disp_daily = daily_base.copy()
    else:
        disp_daily = cross_df.groupby('Day').agg({'DateObj': 'first', 'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index()
        disp_daily = disp_daily[disp_daily['Revenue'] > 0].sort_values('DateObj')
    st.session_state['tb_day_rendered_ids'] = disp_daily['Day'].tolist()

    # 4. SKU Code
    if selected_skus:
        disp_sku = sku_base.copy()
    else:
        disp_sku = cross_df.groupby('SKU').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'}).reset_index()
        disp_sku = disp_sku[disp_sku['Revenue'] > 0]
        disp_sku['Avg CR'] = (disp_sku['Buyers'] / disp_sku['Visitors'] * 100).fillna(0)
        disp_sku['Avg Price'] = (disp_sku['Revenue'] / disp_sku['Units_Sold']).fillna(0)
        disp_sku = disp_sku.sort_values('Revenue', ascending=False)
    st.session_state['tb_sku_rendered_ids'] = disp_sku['SKU'].tolist()

    col_config = {
        "Revenue": st.column_config.NumberColumn("Revenue", format="฿%.2f"),
        "Avg CR": st.column_config.NumberColumn("Avg CR", format="%.2f %%"),
        "Avg Price": st.column_config.NumberColumn("Avg Price", format="฿%.2f"),
        "% Rev": st.column_config.ProgressColumn("%", format="%.1f%%", min_value=0, max_value=100)
    }

    # Middle Row
    col_m1, col_m2, col_m3 = st.columns([1.2, 1.8, 2.5])
    with col_m1:
        st.write("**Order Month**")
        st.dataframe(
            disp_monthly[['Month', 'Revenue', '% Rev', 'Visitors']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_month",
            column_config=col_config
        )
    with col_m2:
        st.write("**Revenue Trend by FGMONTHYEAR**")
        chart_df = cross_df.groupby('Month').agg({'Revenue': 'sum'}).reset_index().sort_values('Month')
        if not chart_df.empty:
            fig = px.line(chart_df, x='Month', y='Revenue', markers=True, text='Revenue', color_discrete_sequence=['#00d4ff'])
            fig.update_traces(textposition="top center", texttemplate='%{text:.2s}')
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0), height=300, 
                xaxis_title="", yaxis_title="",
                xaxis=dict(showgrid=False), yaxis=dict(showgrid=False)
            )
            st.plotly_chart(fig, use_container_width=True)
    with col_m3:
        st.write("**Product Group**")
        st.dataframe(
            disp_prod[['Product', 'Revenue', 'Visitors', 'Avg CR', 'Avg Price', 'A2C', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_prod",
            column_config=col_config
        )

    st.markdown("---")
    # Bottom Row
    col_d1, col_d2 = st.columns([1.5, 2.5])
    with col_d1:
        st.write("**Order Date (รายวัน)**")
        st.dataframe(
            disp_daily[['Day', 'Revenue', 'Visitors', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_day",
            column_config=col_config
        )
    with col_d2:
        st.write("**SKU Code (รายสินค้า)**")
        st.dataframe(
            disp_sku[['SKU', 'Revenue', 'A2C', 'Visitors', 'Avg CR', 'Avg Price', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_sku",
            column_config=col_config
        )
