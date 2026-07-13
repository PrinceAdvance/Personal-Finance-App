import streamlit as st
import pandas as pd
import datetime
import io
import plotly.express as px
import firebase_admin
from firebase_admin import credentials, firestore

from auth import login, logout_button

st.set_page_config(
    page_title="WealthFlow | Personal Finance Command Center",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- FIREBASE INITIALIZATION ---
# st.cache_resource ensures this only runs once per server process,
# avoiding the "app already exists" error on every Streamlit rerun.
@st.cache_resource
def get_db():
    if not firebase_admin._apps:
        cred_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

# --- AUTH GATE + FIRESTORE CONNECTION ---
user_id = login()
db = get_db()

# --- COMPREHENSIVE CATEGORY DEFINITIONS ---
CATEGORIES = {
    "Income": ["Salary", "Investments", "Side Hustle", "Gift", "Offering", "Savings", "Other"],
    "Expense": [
        "Travel & Tourism", 
        "Data", 
        "Tithe", 
        "Offering", 
        "Savings",
        "Bills & Utilities", 
        "Entertainment", 
        "Food & Dining", 
        "Healthcare", 
        "Shopping", 
        "Transportation", 
        "Other"
    ]
}

def _transactions_ref(user_id):
    return db.collection("users").document(user_id).collection("transactions")


def load_transactions(user_id):
    """Loads this user's transaction history from Firestore into a DataFrame.

    The Firestore document id for each transaction is kept as the DataFrame
    index (named '_id') so edits/deletes can be written back to the exact
    same document later.
    """
    docs = _transactions_ref(user_id).stream()
    rows, ids = [], []
    for d in docs:
        rows.append(d.to_dict())
        ids.append(d.id)

    if not rows:
        df = pd.DataFrame(columns=["Date", "Category", "Type", "Amount", "Notes"])
        df.index = pd.Index([], name="_id")
        return df

    df = pd.DataFrame(rows)
    df.index = pd.Index(ids, name="_id")
    df['Date'] = pd.to_datetime(df['Date'])
    return df[["Date", "Category", "Type", "Amount", "Notes"]]


def add_transaction_to_db(user_id, date_val, category, type_val, amount, notes):
    _transactions_ref(user_id).add({
        "Date": pd.to_datetime(date_val).strftime("%Y-%m-%d"),
        "Category": category,
        "Type": type_val,
        "Amount": float(amount),
        "Notes": notes if notes else "-",
    })


def update_transaction_in_db(user_id, doc_id, row):
    _transactions_ref(user_id).document(doc_id).set({
        "Date": pd.to_datetime(row['Date']).strftime("%Y-%m-%d"),
        "Category": row['Category'],
        "Type": row['Type'],
        "Amount": float(row['Amount']),
        "Notes": row['Notes'] if row['Notes'] else "-",
    })


def delete_transaction_from_db(user_id, doc_id):
    _transactions_ref(user_id).document(doc_id).delete()


def delete_all_transactions(user_id):
    for d in _transactions_ref(user_id).stream():
        d.reference.delete()

# --- CUSTOM CSS FOR MODERN PREMIUM UI ---
st.markdown("""
    <style>
    /* Styling Streamlit metric widgets */
    div[data-testid="stMetricValue"] {
        font-size: 2.25rem !important;
        font-weight: 800 !important;
        color: #0F172A !important;
        letter-spacing: -0.05em !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.85rem !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        color: #64748B !important;
    }
    div[data-testid="stMetricDelta"] {
        font-size: 0.9rem !important;
        font-weight: 600 !important;
    }
    
    /* Main layout styling */
    .main {
        background-color: #F8FAFC;
    }
    
    /* Elegant divider lines */
    hr {
        margin-top: 2rem !important;
        margin-bottom: 2rem !important;
        border-color: #E2E8F0 !important;
    }
    
    /* Buttons */
    .stButton>button {
        border-radius: 8px !important;
        transition: all 0.2s ease-in-out !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- INITIAL SESSION STATE SETUP ---
if 'transactions' not in st.session_state:
    st.session_state.transactions = load_transactions(user_id)

if 'currency_symbol' not in st.session_state:
    st.session_state.currency_symbol = "$"

# Initialize manual input field values to prevent uninitialized key assignment bugs
if 'tx_amount' not in st.session_state:
    st.session_state.tx_amount = 0.01
if 'tx_notes' not in st.session_state:
    st.session_state.tx_notes = ""
if 'tx_success_trigger' not in st.session_state:
    st.session_state.tx_success_trigger = False

# --- PARSING & IMPORT ENGINE ---
def process_imported_df(imported_df):
    """
    Intelligently extracts, cleans, maps, and standardizes columns 
    from uploaded or linked spreadsheets.
    """
    date_col = next((c for c in imported_df.columns if 'date' in c.lower()), None)
    
    cat_cols = [c for c in imported_df.columns if 'category' in c.lower()]
    cat_col = None
    for c in cat_cols:
        if not any(s in c.lower() for s in ['.1', '.2', 'sum', 'total', 'grand']):
            cat_col = c
            break
    if not cat_col and cat_cols:
        cat_col = cat_cols
        
    amount_cols = [c for c in imported_df.columns if 'amount' in c.lower()]
    amount_col = None
    for c in amount_cols:
        if not any(s in c.lower() for s in ['sum', 'total', 'grand', 'accumulative']):
            amount_col = c
            break
    if not amount_col and amount_cols:
        amount_col = amount_cols
        
    notes_col = next((c for c in imported_df.columns if any(x in c.lower() for x in ['notes', 'description', 'desc', 'memo', 'particulars', 'narrative'])), None)
    type_col = next((c for c in imported_df.columns if 'type' in c.lower()), None)

    if date_col and cat_col and amount_col:
        mapped_df = pd.DataFrame()
        mapped_df['Date'] = pd.to_datetime(imported_df[date_col], errors='coerce')
        mapped_df['Category'] = imported_df[cat_col].fillna("Other")
        
        if imported_df[amount_col].dtype == object:
            clean_amounts = imported_df[amount_col].astype(str).str.replace(r'[^\d.]', '', regex=True)
            mapped_df['Amount'] = pd.to_numeric(clean_amounts, errors='coerce').fillna(0.0)
        else:
            mapped_df['Amount'] = pd.to_numeric(imported_df[amount_col], errors='coerce').fillna(0.0)
            
        if notes_col:
            mapped_df['Notes'] = imported_df[notes_col].fillna("-")
        else:
            mapped_df['Notes'] = "-"
            
        if type_col:
            mapped_df['Type'] = imported_df[type_col].fillna("Expense")
        else:
            income_cats_lower = [cat.lower().strip() for cat in CATEGORIES["Income"]]
            mapped_df['Type'] = mapped_df['Category'].apply(
                lambda x: "Income" if str(x).lower().strip() in income_cats_lower else "Expense"
            )

        def standardize_category(row):
            cat_str = str(row['Category']).strip()
            tx_type = row['Type']
            
            for standard_cat in CATEGORIES[tx_type]:
                if cat_str.lower() == standard_cat.lower():
                    return standard_cat
            
            aliases = {
                "travel": "Travel & Tourism",
                "traveling": "Travel & Tourism",
                "bills": "Bills & Utilities",
                "utilities": "Bills & Utilities",
                "food": "Food & Dining",
                "dining": "Food & Dining",
                "grocery": "Food & Dining",
                "groceries": "Food & Dining",
                "transport": "Transportation",
                "tithe": "Tithe",
                "offering": "Offering",
                "savings": "Savings",
                "data": "Data",
                "internet": "Data"
            }
            if cat_str.lower() in aliases:
                return aliases[cat_str.lower()]
            return "Other"

        mapped_df['Category'] = mapped_df.apply(standardize_category, axis=1)
        mapped_df = mapped_df.dropna(subset=['Date', 'Amount'])
        mapped_df = mapped_df[mapped_df['Date'].notna()]
        
        required_columns = ["Date", "Category", "Type", "Amount", "Notes"]
        final_df = mapped_df[required_columns].copy()
        final_df['Date'] = pd.to_datetime(final_df['Date'])
        
        detected_currency = None
        if "ghs" in amount_col.lower() or "₵" in amount_col.lower():
            detected_currency = "₵"
        elif "usd" in amount_col.lower() or "$" in amount_col.lower():
            detected_currency = "$"
        elif "eur" in amount_col.lower() or "€" in amount_col.lower():
            detected_currency = "€"
        elif "gbp" in amount_col.lower() or "£" in amount_col.lower():
            detected_currency = "£"
            
        return final_df, detected_currency
    else:
        return None, None

# --- TRANSACTION SUBMISSION CALLBACK ---
def handle_add_tx_callback():
    """Adds a transaction to Firestore and refreshes the in-memory table."""
    date_val = st.session_state.tx_date
    type_val = st.session_state.tx_type
    cat_val = st.session_state.tx_category
    amount_val = st.session_state.tx_amount
    notes_val = st.session_state.tx_notes

    add_transaction_to_db(user_id, date_val, cat_val, type_val, amount_val, notes_val)
    st.session_state.transactions = load_transactions(user_id)

    # Clear visual sidebar input states
    st.session_state.tx_amount = 0.01
    st.session_state.tx_notes = ""
    st.session_state.tx_success_trigger = True

# --- SIDEBAR INTERFACE ---
with st.sidebar:
    st.caption(f"Logged in as **{user_id}**")
    logout_button()
    st.markdown("---")
    st.markdown("## ⚙️ System Settings")
    
    currency_options = ["$", "€", "£", "¥", "₵", "₦", "₹", "₱", "zł"]
    
    default_currency_idx = currency_options.index(st.session_state.currency_symbol) if st.session_state.currency_symbol in currency_options else 0
    selected_currency = st.selectbox("💱 Dashboard Currency", currency_options, index=default_currency_idx)
    st.session_state.currency_symbol = selected_currency
    
    st.markdown("---")
    st.markdown("## ✨ Add Transaction")
    
    col_date, col_type = st.columns(2)
    with col_date:
        tx_date = st.date_input("Date", datetime.date.today(), key="tx_date")
    with col_type:
        tx_type = st.selectbox("Type", ["Expense", "Income"], key="tx_type")
        
    available_cats = CATEGORIES[tx_type]
    tx_category = st.selectbox("Category", available_cats, key="tx_category")
    
    tx_amount = st.number_input(f"Amount ({selected_currency})", min_value=0.01, step=10.00, format="%.2f", key="tx_amount")
    tx_notes = st.text_input("Notes / Description", placeholder="e.g. Spotify, Weekly grocery list", key="tx_notes")
    
    submit_tx = st.button(label="Add to Ledger", use_container_width=True, type="primary", on_click=handle_add_tx_callback)

    if st.session_state.tx_success_trigger:
        st.toast("Transaction logged and saved successfully!", icon="✅")
        st.session_state.tx_success_trigger = False

    st.markdown("---")
    st.markdown("## 📂 CSV Spreadsheet Import")
    
    uploaded_file = st.file_uploader(
        "📤 Upload local CSV file", 
        type=["csv"], 
        help="Upload any standard exported CSV spreadsheet files."
    )
    if uploaded_file is not None:
        try:
            imported_df = pd.read_csv(uploaded_file)
            final_df, detected_curr = process_imported_df(imported_df)
            
            if final_df is not None:
                for _, row in final_df.iterrows():
                    add_transaction_to_db(user_id, row['Date'], row['Category'], row['Type'], row['Amount'], row['Notes'])
                st.session_state.transactions = load_transactions(user_id)

                if detected_curr:
                    st.session_state.currency_symbol = detected_curr
                st.toast("Ledger spreadsheet imported and saved!", icon="📂")
                st.rerun()
            else:
                st.error("Failed to parse file. Ensure headers contain 'Date', 'Category' and 'Amount'.")
        except Exception as e:
            st.error(f"Failed to process CSV file: {e}")

    st.markdown("---")
    
    if not st.session_state.transactions.empty:
        export_df = st.session_state.transactions.copy()
        export_df['Date'] = pd.to_datetime(export_df['Date'])
        export_df['Date'] = export_df['Date'].dt.strftime('%Y-%m-%d')
        csv_data = export_df.to_csv(index=False)
        
        st.download_button(
            label="📥 Export Ledger to CSV",
            data=csv_data,
            file_name="wealthflow_ledger.csv",
            mime="text/csv",
            use_container_width=True
        )
        
    if st.button("🗑️ Reset Application Ledger", type="secondary", use_container_width=True):
        delete_all_transactions(user_id)
        st.session_state.transactions = load_transactions(user_id)
        st.toast("Ledger database cleared.", icon="🗑️")
        st.rerun()

# --- MAIN DASHBOARD INTERFACE ---
st.title("💰 WealthFlow")
st.subheader("Your Personal Financial Command Center")
st.markdown("Keep track of your cash flows, savings structure, and capital allocation with visual analytics.")
st.markdown("---")

df = st.session_state.transactions

# Ensure column types are explicitly clean before analytical computations
if not df.empty:
    df['Date'] = pd.to_datetime(df['Date'])
    total_income = df[df['Type'] == 'Income']['Amount'].sum()
    total_expense = df[df['Type'] == 'Expense']['Amount'].sum()
    net_savings = total_income - total_expense
    savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0.0
else:
    total_income = total_expense = net_savings = savings_rate = 0.0

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Total Inflows", 
        value=f"{selected_currency}{total_income:,.2f}", 
        help="The sum of all logged salary, investments, and external income streams."
    )
with col2:
    st.metric(
        label="Total Outflows", 
        value=f"{selected_currency}{total_expense:,.2f}", 
        help="The sum of all recorded household expenses and purchases."
    )
with col3:
    delta_color = "normal" if net_savings >= 0 else "inverse"
    st.metric(
        label="Net Capital Delta", 
        value=f"{selected_currency}{net_savings:,.2f}", 
        delta=f"{selected_currency}{net_savings:,.2f} Net Profit" if net_savings >= 0 else f"{selected_currency}{net_savings:,.2f} Deficit",
        delta_color=delta_color,
        help="Net Income minus Net Expenses."
    )
with col4:
    st.metric(
        label="Savings Rate", 
        value=f"{savings_rate:.1f}%",
        help="Percentage of your total income saved over time. Target 20%+."
    )

st.markdown("---")

if not df.empty:
    chart_col1, chart_col2 = st.columns(2)
    
    with chart_col1:
        st.markdown("### 📈 Cash Flow Timeline")
        trend_data = df.groupby([df['Date'].dt.date, 'Type'])['Amount'].sum().reset_index()
        trend_data = trend_data.sort_values(by="Date")
        
        fig_trend = px.line(
            trend_data, 
            x='Date', 
            y='Amount', 
            color='Type',
            markers=True,
            color_discrete_map={'Income': '#10B981', 'Expense': '#EF4444'},
            labels={'Amount': f'Amount ({selected_currency})', 'Date': 'Timeline'}
        )
        
        fig_trend.update_layout(
            hovermode="x unified",
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig_trend.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#E2E8F0')
        fig_trend.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#E2E8F0')
        st.plotly_chart(fig_trend, use_container_width=True)

    with chart_col2:
        st.markdown("### 🍕 Capital Allocation Breakdown")
        
        expense_df = df[df['Type'] == 'Expense']
        if not expense_df.empty:
            category_data = expense_df.groupby('Category')['Amount'].sum().reset_index()
            
            fig_pie = px.pie(
                category_data, 
                values='Amount', 
                names='Category',
                hole=0.5,
                color_discrete_sequence=px.colors.qualitative.Safe
            )
            fig_pie.update_traces(
                textposition='inside', 
                textinfo='percent+label',
                hovertemplate=f"Category: %{{label}}<br>Amount: {selected_currency}%{{value:,.2f}}<br>Percentage: %{{percent}}"
            )
            fig_pie.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=False
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("💡 Add some expenses via the sidebar to view your capital allocation structure chart.")
else:
    st.info("📌 Your ledger is currently empty. Add transaction entries or import a spreadsheet in the sidebar to populate your dashboard!")

st.markdown("---")

st.markdown("### 📜 Interactive Ledger History")

if not df.empty:
    st.markdown("*💡 Tip: Double-click any cell to directly edit the values. Your updates save to the database in real-time!*")
    
    sorted_df = df.sort_values(by="Date", ascending=False).copy()
    sorted_df['Date'] = pd.to_datetime(sorted_df['Date'])
    
    # Render with st.data_editor to allow live cell-by-cell spreadsheet modifications
    edited_df = st.data_editor(
        sorted_df,
        column_config={
            "Amount": st.column_config.NumberColumn(f"Amount ({selected_currency})", format=f"{selected_currency}%.2f"),
            "Type": st.column_config.SelectboxColumn("Type", options=["Income", "Expense"]),
            "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES["Income"] + CATEGORIES["Expense"]),
            "Notes": st.column_config.TextColumn("Description"),
            "Date": st.column_config.DateColumn("Date")
        },
        use_container_width=True,
        hide_index=True,
        key="ledger_data_editor"
    )
    
    # Auto-save changed rows back to their specific Firestore documents
    if not edited_df.equals(sorted_df):
        for doc_id in edited_df.index:
            if not edited_df.loc[doc_id].equals(sorted_df.loc[doc_id]):
                update_transaction_in_db(user_id, doc_id, edited_df.loc[doc_id])
        st.session_state.transactions = load_transactions(user_id)
        st.toast("Spreadsheet edits auto-saved!", icon="💾")
        st.rerun()

    # --- SIMPLIFIED TRANSACTION DELETION TOOL ---
    st.markdown("---")
    st.markdown("### 🗑️ Delete a Transaction")
    
    # Bind dropdown directly to dataframe indexes, using formatted labels cleanly without splitting/replace bugs
    transaction_to_delete = st.selectbox(
        "Select a transaction to permanently delete:",
        options=df.index,
        format_func=lambda idx: f"{df.loc[idx, 'Date'].strftime('%Y-%m-%d')} | {df.loc[idx, 'Category']} | {selected_currency}{df.loc[idx, 'Amount']:,.2f} | {df.loc[idx, 'Notes']}"
    )
    
    col_del1, col_del2 = st.columns(2)
    with col_del1:
        delete_btn = st.button("❌ Delete Transaction", type="secondary", use_container_width=True)
        
    if delete_btn:
        # transaction_to_delete is the Firestore document id (the DataFrame index)
        delete_transaction_from_db(user_id, transaction_to_delete)
        st.session_state.transactions = load_transactions(user_id)

        st.toast("Transaction successfully deleted!", icon="🗑️")
        st.rerun()

else:
    st.warning("No recorded transaction history found.")