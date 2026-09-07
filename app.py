import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Shopee Dashboard (Lazada Style)", layout="wide")

# ================= 0. Simple Authentication =================
def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        if st.session_state["password"] == "1234":  # เปลี่ยนรหัสผ่านตรงนี้ได้เลยครับ
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # ลบรหัสออกจาก memory เพื่อความปลอดภัย
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
    
    
    @st.cache_data
    def load_data():
        df = pd.read_csv('Master_Shopee_Data.csv')
        
        cols_to_clean = {
            'Revenue': 'ยอดขาย (ที่มีการสั่งซื้อทั้งหมด) (THB)',
            'Visitors': 'ผู้เข้าชมสินค้า',
            'Buyers': 'ผู้ซื้อ (ที่มีการสั่งซื้อทั้งหมด)',
            'Units_Sold': 'จำนวนที่ขายได้ (ที่มีการสั่งซื้อทั้งหมด)',
            'Orders': 'ทั้งหมด' 
        }
        
        for en_col, th_col in cols_to_clean.items():
            if th_col in df.columns:
                df[en_col] = df[th_col].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                
        cr_col = 'อัตราการซื้อสินค้า (ทั้งหมด)'
        if cr_col in df.columns:
            df['CR'] = df[cr_col].astype(str).str.replace('%', '').str.replace('-', '0').astype(float) / 100.0
            
        df['DateObj'] = pd.to_datetime(df['Date'], format='mixed', dayfirst=True, errors='coerce')
        df = df.dropna(subset=['DateObj']).copy()
        
        df['Year'] = df['DateObj'].dt.year.astype(str)
        df['Month'] = df['DateObj'].dt.strftime('%Y-%m')
        df['Day'] = df['DateObj'].dt.strftime('%d/%m/%Y')
        
        df['SKU'] = df['SKU'].astype(str)
        df['Parent_SKU'] = df['Parent SKU'].astype(str)
        df['Product'] = df['ผลิตภัณฑ์'].astype(str)
        
        return df
    
    base_df = load_data()
    
    st.markdown("### 🛒 Shopee Performance Dashboard (Power BI Style)")
    
    # ================= 1. Global Filters =================
    st.markdown("---")
    col_f1, col_f2 = st.columns([1, 3])
    with col_f1:
        all_years = sorted(base_df['Year'].unique())
        selected_years = st.multiselect("ปี (Year)", all_years, default=all_years)
    
    with col_f2:
        all_parents = sorted(list(set([str(s) for s in base_df['Parent_SKU'].unique() if str(s) not in ['-', 'nan', 'NaN']])))
        selected_parents = st.multiselect("กลุ่มสินค้า (Parent SKU / Category)", all_parents)
    
    filtered_df = base_df.copy()
    if selected_years:
        filtered_df = filtered_df[filtered_df['Year'].isin(selected_years)]
    if selected_parents:
        filtered_df = filtered_df[filtered_df['Parent_SKU'].isin(selected_parents)]
    
    # ================= 2. Base Aggregations (Static for row mapping) =================
    # We must keep the row order fixed so Streamlit selection indices don't break
    monthly_base = filtered_df.groupby('Month').agg({'Revenue': 'sum'}).reset_index()
    prod_base = filtered_df.groupby('Product').agg({'Revenue': 'sum'}).reset_index().sort_values('Revenue', ascending=False)
    daily_base = filtered_df.groupby('Day').agg({'DateObj': 'first'}).reset_index().sort_values('DateObj')
    sku_base = filtered_df.groupby('SKU').agg({'Revenue': 'sum'}).reset_index().sort_values('Revenue', ascending=False)
    
    # ================= 3. Get Selections =================
    sel_month_idx = st.session_state.get('tb_month', {}).get('selection', {}).get('rows', [])
    sel_prod_idx = st.session_state.get('tb_prod', {}).get('selection', {}).get('rows', [])
    sel_day_idx = st.session_state.get('tb_day', {}).get('selection', {}).get('rows', [])
    sel_sku_idx = st.session_state.get('tb_sku', {}).get('selection', {}).get('rows', [])
    
    selected_months = monthly_base.iloc[sel_month_idx]['Month'].tolist() if sel_month_idx else []
    selected_prods = prod_base.iloc[sel_prod_idx]['Product'].tolist() if sel_prod_idx else []
    selected_days = daily_base.iloc[sel_day_idx]['Day'].tolist() if sel_day_idx else []
    selected_skus = sku_base.iloc[sel_sku_idx]['SKU'].tolist() if sel_sku_idx else []
    
    # ================= 4. Build Cross-Filtered DataFrame =================
    cross_df = filtered_df.copy()
    if selected_months: cross_df = cross_df[cross_df['Month'].isin(selected_months)]
    if selected_prods: cross_df = cross_df[cross_df['Product'].isin(selected_prods)]
    if selected_days: cross_df = cross_df[cross_df['Day'].isin(selected_days)]
    if selected_skus: cross_df = cross_df[cross_df['SKU'].isin(selected_skus)]
    
    # ================= 5. KPI Scorecards =================
    st.markdown("---")
    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6, kpi7 = st.columns(7)
    
    rev = cross_df['Revenue'].sum()
    vis = cross_df['Visitors'].sum()
    buy = cross_df['Buyers'].sum()
    unit = cross_df['Units_Sold'].sum()
    orders = cross_df['Orders'].sum()
    
    cr = (buy / vis) if vis > 0 else 0
    rev_per_buyer = (rev / buy) if buy > 0 else 0
    aov = (rev / orders) if orders > 0 else 0
    
    kpi1.metric("Revenue (ยอดขาย)", f"฿{rev:,.0f}")
    kpi2.metric("SKU Visitors", f"{vis:,.0f}")
    kpi3.metric("SKU CR%", f"{cr*100:,.2f}%")
    kpi4.metric("Rev per Buyers", f"฿{rev_per_buyer:,.0f}")
    kpi5.metric("AOV", f"฿{aov:,.0f}")
    kpi6.metric("Buyers (ผู้ซื้อ)", f"{buy:,.0f}")
    kpi7.metric("Units Sold", f"{unit:,.0f}")
    
    st.markdown("---")
    
    # ================= 6. Create Display Tables (Join Base with Cross_df) =================
    # This technique ensures the table sizes/orders NEVER change, preventing selection index bugs,
    # while updating the values dynamically just like Power BI!
    
    # Monthly Display
    monthly_cross = cross_df.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index()
    disp_monthly = monthly_base[['Month']].merge(monthly_cross, on='Month', how='left').fillna(0)
    disp_monthly['% Rev'] = (disp_monthly['Revenue'] / disp_monthly['Revenue'].sum() * 100).fillna(0)
    
    # Product Display
    prod_cross = cross_df.groupby('Product').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Units_Sold': 'sum'}).reset_index()
    disp_prod = prod_base[['Product']].merge(prod_cross, on='Product', how='left').fillna(0)
    
    # Daily Display
    daily_cross = cross_df.groupby('Day').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index()
    disp_daily = daily_base[['Day']].merge(daily_cross, on='Day', how='left').fillna(0)
    
    # SKU Display
    sku_cross = cross_df.groupby('SKU').agg({'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index()
    disp_sku = sku_base[['SKU']].merge(sku_cross, on='SKU', how='left').fillna(0)
    
    # ================= 7. Render UI =================
    col_m1, col_m2, col_m3 = st.columns([1, 1.5, 1.5])
    
    with col_m1:
        st.write("**Order Month**")
        st.dataframe(
            disp_monthly[['Month', 'Revenue', '% Rev', 'Visitors']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_month"
        )
    
    with col_m2:
        st.write("**LAZ Revenue by FGMONTHYEAR**")
        if not disp_monthly.empty:
            fig = px.line(disp_monthly, x='Month', y='Revenue', markers=True)
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=300)
            st.plotly_chart(fig, use_container_width=True)
    
    with col_m3:
        st.write("**Product Group**")
        st.dataframe(
            disp_prod[['Product', 'Revenue', 'Visitors', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_prod"
        )
    
    st.markdown("---")
    col_d1, col_d2 = st.columns([1, 1.5])
    
    with col_d1:
        st.write("**Order Date (รายวัน)**")
        st.dataframe(
            disp_daily[['Day', 'Revenue', 'Visitors', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_day"
        )
    
    with col_d2:
        st.write("**SKU Code (รายสินค้า)**")
        st.dataframe(
            disp_sku[['SKU', 'Revenue', 'Visitors', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_sku"
        )
