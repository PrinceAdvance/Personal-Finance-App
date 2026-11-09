import streamlit as st
import pandas as pd
import datetime
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

# --- CATEGORY DEFINITIONS ---
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

PERIOD_PRESETS = [
    "All time",
    "This month",
    "Last month",
    "Last 30 days",
    "Last 90 days",
    "This year",
    "Custom range",
]

# =====================================================================
# FIRESTORE HELPERS
# =====================================================================
def _transactions_ref(user_id):
    return db.collection("users").document(user_id).collection("transactions")


def _budgets_ref(user_id):
    return db.collection("users").document(user_id).collection("budgets")


def _tx_payload(date_val, category, type_val, amount, notes):
    return {
        "Date": pd.to_datetime(date_val).strftime("%Y-%m-%d"),
        "Category": category,
        "Type": type_val,
        "Amount": float(amount),
        "Notes": notes if notes else "-",
    }


def load_transactions(user_id):
    """Loads this user's transactions into a DataFrame indexed by Firestore doc id ('_id')."""
    docs = _transactions_ref(user_id).stream()
    rows, ids = [], []
    for d in docs:
        rows.append(d.to_dict())
        ids.append(d.id)

    if not rows:
        df = pd.DataFrame(columns=["Date", "Category", "Type", "Amount", "Notes"])
        df.index = pd.Index([], name="_id")
        df['Date'] = pd.to_datetime(df['Date'])
        return df

    df = pd.DataFrame(rows)
    df.index = pd.Index(ids, name="_id")
    df['Date'] = pd.to_datetime(df['Date'])
    return df[["Date", "Category", "Type", "Amount", "Notes"]]


def add_transaction_to_db(user_id, date_val, category, type_val, amount, notes):
    _transactions_ref(user_id).add(_tx_payload(date_val, category, type_val, amount, notes))


def add_transactions_batch(user_id, rows_df):
    """Writes many transactions using Firestore batched writes (max 500 ops per batch)."""
    batch = db.batch()
    pending = 0
    for _, row in rows_df.iterrows():
        ref = _transactions_ref(user_id).document()
        batch.set(ref, _tx_payload(row['Date'], row['Category'], row['Type'], row['Amount'], row['Notes']))
        pending += 1
        if pending == 400:
            batch.commit()
            batch = db.batch()
            pending = 0
    if pending:
        batch.commit()


def update_transaction_in_db(user_id, doc_id, row):
    _transactions_ref(user_id).document(doc_id).set(
        _tx_payload(row['Date'], row['Category'], row['Type'], row['Amount'], row['Notes'])
    )


def delete_transaction_from_db(user_id, doc_id):
    _transactions_ref(user_id).document(doc_id).delete()


def delete_all_transactions(user_id):
    for d in _transactions_ref(user_id).stream():
        d.reference.delete()


# --- Budgets: one doc per category, doc id = category name, field 'Limit' = monthly limit ---
def _budget_doc_id(category):
    return category.replace("/", "-")


def load_budgets(user_id):
    budgets = {}
    for d in _budgets_ref(user_id).stream():
        data = d.to_dict()
        budgets[data.get("Category", d.id)] = float(data.get("Limit", 0.0))
    return budgets


def save_budgets(user_id, limits):
    """limits: {category: monthly_limit}. A limit of 0 removes that category's budget."""
    batch = db.batch()
    for cat, limit in limits.items():
        ref = _budgets_ref(user_id).document(_budget_doc_id(cat))
        if limit and limit > 0:
            batch.set(ref, {"Category": cat, "Limit": float(limit)})
        else:
            batch.delete(ref)
    batch.commit()


# =====================================================================
# CUSTOM CSS
# =====================================================================
st.markdown("""
    <style>
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
    .main { background-color: #F8FAFC; }
    hr {
        margin-top: 2rem !important;
        margin-bottom: 2rem !important;
        border-color: #E2E8F0 !important;
    }
    .stButton>button {
        border-radius: 8px !important;
        transition: all 0.2s ease-in-out !important;
    }
    </style>
""", unsafe_allow_html=True)

# =====================================================================
# SESSION STATE
# =====================================================================
if 'transactions' not in st.session_state:
    st.session_state.transactions = load_transactions(user_id)

if 'budgets' not in st.session_state:
    st.session_state.budgets = load_budgets(user_id)

if 'currency_symbol' not in st.session_state:
    st.session_state.currency_symbol = "$"

if 'tx_amount' not in st.session_state:
    st.session_state.tx_amount = 0.01
if 'tx_notes' not in st.session_state:
    st.session_state.tx_notes = ""
if 'tx_success_trigger' not in st.session_state:
    st.session_state.tx_success_trigger = False

# =====================================================================
# DATE RANGE LOGIC
# =====================================================================
def compute_preset_range(preset, all_df):
    today = datetime.date.today()
    if preset == "This month":
        return today.replace(day=1), today
    if preset == "Last month":
        end = today.replace(day=1) - datetime.timedelta(days=1)
        return end.replace(day=1), end
    if preset == "Last 30 days":
        return today - datetime.timedelta(days=29), today
    if preset == "Last 90 days":
        return today - datetime.timedelta(days=89), today
    if preset == "This year":
        return today.replace(month=1, day=1), today
    # All time
    if all_df.empty:
        return today, today
    return all_df['Date'].min().date(), max(all_df['Date'].max().date(), today)


def month_options(all_df):
    """Returns 'YYYY-MM' strings, newest first, from the earliest transaction (or 5 months ago) to now."""
    cur = pd.Timestamp(datetime.date.today()).to_period("M")
    start = cur - 5
    end = cur
    if not all_df.empty:
        start = min(start, all_df['Date'].min().to_period("M"))
        end = max(end, all_df['Date'].max().to_period("M"))
    return [str(p) for p in pd.period_range(start, end, freq="M")][::-1]


# --- PARSING & IMPORT ENGINE ---
def process_imported_df(imported_df):
    """Extracts, cleans, maps, and standardizes columns from an uploaded spreadsheet."""
    date_col = next((c for c in imported_df.columns if 'date' in c.lower()), None)

    cat_cols = [c for c in imported_df.columns if 'category' in c.lower()]
    cat_col = None
    for c in cat_cols:
        if not any(s in c.lower() for s in ['.1', '.2', 'sum', 'total', 'grand']):
            cat_col = c
            break
    if not cat_col and cat_cols:
        cat_col = cat_cols[0]

    amount_cols = [c for c in imported_df.columns if 'amount' in c.lower()]
    amount_col = None
    for c in amount_cols:
        if not any(s in c.lower() for s in ['sum', 'total', 'grand', 'accumulative']):
            amount_col = c
            break
    if not amount_col and amount_cols:
        amount_col = amount_cols[0]

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
            tx_type = row['Type'] if row['Type'] in CATEGORIES else "Expense"

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

        required_columns = ["Date", "Category", "Type", "Amount", "Notes"]
        final_df = mapped_df[required_columns].copy()
        final_df['Date'] = pd.to_datetime(final_df['Date'])

        detected_currency = None
        amt_lower = amount_col.lower()
        if "ghs" in amt_lower or "₵" in amt_lower:
            detected_currency = "₵"
        elif "usd" in amt_lower or "$" in amt_lower:
            detected_currency = "$"
        elif "eur" in amt_lower or "€" in amt_lower:
            detected_currency = "€"
        elif "gbp" in amt_lower or "£" in amt_lower:
            detected_currency = "£"

        return final_df, detected_currency
    else:
        return None, None


# --- TRANSACTION SUBMISSION CALLBACK ---
def handle_add_tx_callback():
    date_val = st.session_state.tx_date
    type_val = st.session_state.tx_type
    cat_val = st.session_state.tx_category
    amount_val = st.session_state.tx_amount
    notes_val = st.session_state.tx_notes

    add_transaction_to_db(user_id, date_val, cat_val, type_val, amount_val, notes_val)
    st.session_state.transactions = load_transactions(user_id)

    st.session_state.tx_amount = 0.01
    st.session_state.tx_notes = ""
    st.session_state.tx_success_trigger = True


# =====================================================================
# SIDEBAR
# =====================================================================
all_df = st.session_state.transactions

with st.sidebar:
    st.caption(f"Logged in as **{user_id}**")
    logout_button()
    st.markdown("---")
    st.markdown("## ⚙️ System Settings")

    currency_options = ["$", "€", "£", "¥", "₵", "₦", "₹", "₱", "zł"]
    default_currency_idx = currency_options.index(st.session_state.currency_symbol) if st.session_state.currency_symbol in currency_options else 0
    selected_currency = st.selectbox("💱 Dashboard Currency", currency_options, index=default_currency_idx)
    st.session_state.currency_symbol = selected_currency

    # ---------- VIEW PERIOD / FILTERS ----------
    st.markdown("---")
    st.markdown("## 📅 View Period")
    preset = st.selectbox("Show transactions for", PERIOD_PRESETS, key="period_preset")

    if preset == "Custom range":
        default_start, default_end = compute_preset_range("This month", all_df)
        picked = st.date_input("Select date range", value=(default_start, default_end), key="custom_range")
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            range_start, range_end = picked
        else:
            single = picked[0] if isinstance(picked, (tuple, list)) else picked
            range_start = range_end = single
            st.caption("Pick an end date to complete the range.")
    else:
        range_start, range_end = compute_preset_range(preset, all_df)

    available_filter_cats = sorted(all_df['Category'].unique().tolist()) if not all_df.empty else []
    selected_filter_cats = st.multiselect(
        "Filter by category (optional)",
        options=available_filter_cats,
        placeholder="All categories",
        key="category_filter"
    )

    # ---------- ADD TRANSACTION ----------
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

    # ---------- CSV IMPORT ----------
    st.markdown("---")
    st.markdown("## 📂 CSV Spreadsheet Import")

    uploaded_file = st.file_uploader(
        "📤 Upload local CSV file",
        type=["csv"],
        help="Upload any standard exported CSV spreadsheet files."
    )
    if uploaded_file is None:
        st.session_state.pop("last_imported_file", None)
    else:
        # Guard: without this, st.rerun() below would re-import the same file forever,
        # because the uploader still holds the file on every rerun.
        file_key = f"{uploaded_file.name}-{uploaded_file.size}"
        if st.session_state.get("last_imported_file") != file_key:
            try:
                imported_df = pd.read_csv(uploaded_file)
                final_df, detected_curr = process_imported_df(imported_df)

                if final_df is not None:
                    add_transactions_batch(user_id, final_df)
                    st.session_state.last_imported_file = file_key
                    st.session_state.transactions = load_transactions(user_id)

                    if detected_curr:
                        st.session_state.currency_symbol = detected_curr
                    st.toast(f"Imported {len(final_df)} transactions!", icon="📂")
                    st.rerun()
                else:
                    st.error("Failed to parse file. Ensure headers contain 'Date', 'Category' and 'Amount'.")
            except Exception as e:
                st.error(f"Failed to process CSV file: {e}")

    # ---------- EXPORT / RESET ----------
    st.markdown("---")

    if not all_df.empty:
        export_df = all_df.copy()
        export_df['Date'] = pd.to_datetime(export_df['Date']).dt.strftime('%Y-%m-%d')
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

# =====================================================================
# APPLY FILTERS
# =====================================================================
if not all_df.empty:
    all_df['Date'] = pd.to_datetime(all_df['Date'])
    date_mask = (all_df['Date'].dt.date >= range_start) & (all_df['Date'].dt.date <= range_end)
    df = all_df[date_mask]
    if selected_filter_cats:
        df = df[df['Category'].isin(selected_filter_cats)]
else:
    df = all_df

# =====================================================================
# MAIN DASHBOARD
# =====================================================================
st.title("💰 WealthFlow")
st.subheader("Your Personal Financial Command Center")
st.markdown("Keep track of your cash flows, savings structure, and capital allocation with visual analytics.")

filter_note = f" · Categories: {', '.join(selected_filter_cats)}" if selected_filter_cats else ""
st.caption(
    f"📅 Showing **{range_start:%d %b %Y}** → **{range_end:%d %b %Y}** · "
    f"**{len(df)}** transaction(s){filter_note}"
)
st.markdown("---")

if not df.empty:
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
        help="The sum of all logged salary, investments, and external income streams in the selected period."
    )
with col2:
    st.metric(
        label="Total Outflows",
        value=f"{selected_currency}{total_expense:,.2f}",
        help="The sum of all recorded expenses in the selected period."
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
        help="Percentage of your income saved in the selected period. Target 20%+."
    )

st.markdown("---")

tab_overview, tab_budget, tab_ledger = st.tabs(["📊 Overview", "🎯 Budgets", "📜 Ledger"])

# ---------------------------------------------------------------------
# TAB 1: OVERVIEW
# ---------------------------------------------------------------------
with tab_overview:
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
                st.info("💡 No expenses in this period to chart.")

        st.markdown("### 🗓️ Monthly Summary")
        monthly = (
            df.assign(Month=df['Date'].dt.strftime('%Y-%m'))
            .groupby(['Month', 'Type'])['Amount'].sum().reset_index()
            .sort_values('Month')
        )
        fig_monthly = px.bar(
            monthly,
            x='Month',
            y='Amount',
            color='Type',
            barmode='group',
            color_discrete_map={'Income': '#10B981', 'Expense': '#EF4444'},
            labels={'Amount': f'Amount ({selected_currency})'}
        )
        fig_monthly.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig_monthly.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#E2E8F0')
        st.plotly_chart(fig_monthly, use_container_width=True)
    else:
        if all_df.empty:
            st.info("📌 Your ledger is currently empty. Add transaction entries or import a spreadsheet in the sidebar to populate your dashboard!")
        else:
            st.info("📭 No transactions match the selected period/filters. Try widening the date range in the sidebar.")

# ---------------------------------------------------------------------
# TAB 2: BUDGETS
# ---------------------------------------------------------------------
with tab_budget:
    st.markdown("### 🎯 Monthly Budgets")
    st.caption(
        "Set a monthly spending limit per expense category. Progress is tracked for the month you pick below, "
        "independent of the sidebar date range."
    )

    budgets = st.session_state.budgets

    # --- Budget editor ---
    with st.expander("✏️ Set / edit monthly budgets", expanded=not budgets):
        st.caption("Enter a monthly limit for each category. Set to 0 to remove a budget.")
        with st.form("budget_form"):
            form_cols = st.columns(2)
            new_limits = {}
            for i, cat in enumerate(CATEGORIES["Expense"]):
                with form_cols[i % 2]:
                    new_limits[cat] = st.number_input(
                        f"{cat} ({selected_currency})",
                        min_value=0.0,
                        value=float(budgets.get(cat, 0.0)),
                        step=50.0,
                        format="%.2f",
                        key=f"budget_input_{cat}"
                    )
            saved = st.form_submit_button("💾 Save budgets", type="primary", use_container_width=True)

        if saved:
            save_budgets(user_id, new_limits)
            st.session_state.budgets = load_budgets(user_id)
            st.toast("Budgets saved!", icon="🎯")
            st.rerun()

    # --- Month selector ---
    months = month_options(all_df)
    current_month_str = str(pd.Timestamp(datetime.date.today()).to_period("M"))
    default_month_idx = months.index(current_month_str) if current_month_str in months else 0
    selected_month = st.selectbox(
        "Budget month",
        options=months,
        index=default_month_idx,
        format_func=lambda m: pd.Period(m, freq="M").strftime("%B %Y"),
        key="budget_month"
    )

    if not budgets:
        st.info("🎯 No budgets set yet. Open the editor above to create your first one.")
    else:
        if all_df.empty:
            month_expenses = pd.Series(dtype=float)
        else:
            month_mask = (all_df['Type'] == 'Expense') & (all_df['Date'].dt.strftime('%Y-%m') == selected_month)
            month_expenses = all_df[month_mask].groupby('Category')['Amount'].sum()

        rows = []
        for cat, limit in budgets.items():
            spent = float(month_expenses.get(cat, 0.0))
            pct = (spent / limit) if limit > 0 else 0.0
            rows.append({"Category": cat, "Budget": limit, "Spent": spent, "Remaining": limit - spent, "Pct": pct})
        budget_df = pd.DataFrame(rows).sort_values("Pct", ascending=False)

        total_budget = budget_df["Budget"].sum()
        total_spent_budgeted = budget_df["Spent"].sum()
        total_remaining = total_budget - total_spent_budgeted
        unbudgeted_spend = float(month_expenses[~month_expenses.index.isin(budgets.keys())].sum()) if len(month_expenses) else 0.0
        overall_pct = (total_spent_budgeted / total_budget * 100) if total_budget > 0 else 0.0

        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Total Budget", f"{selected_currency}{total_budget:,.2f}")
        b2.metric("Spent (Budgeted)", f"{selected_currency}{total_spent_budgeted:,.2f}", help=f"{overall_pct:.1f}% of total budget used")
        b3.metric(
            "Remaining" if total_remaining >= 0 else "Over Budget",
            f"{selected_currency}{abs(total_remaining):,.2f}"
        )
        b4.metric("Unbudgeted Spend", f"{selected_currency}{unbudgeted_spend:,.2f}", help="Expenses in categories that have no budget set.")

        over = budget_df[budget_df["Pct"] > 1.0]
        near = budget_df[(budget_df["Pct"] >= 0.8) & (budget_df["Pct"] <= 1.0)]
        if not over.empty:
            st.error("🔴 Over budget: " + ", ".join(
                f"**{r.Category}** (+{selected_currency}{-r.Remaining:,.2f})" for r in over.itertuples()
            ))
        if not near.empty:
            st.warning("🟡 Nearing limit (80%+): " + ", ".join(f"**{c}**" for c in near["Category"]))

        st.markdown("#### Category Progress")
        for r in budget_df.itertuples():
            icon = "🟢" if r.Pct < 0.8 else ("🟡" if r.Pct <= 1.0 else "🔴")
            c1, c2, c3 = st.columns([2, 5, 3])
            c1.markdown(f"**{r.Category}**")
            c2.progress(min(r.Pct, 1.0))
            left_txt = (
                f"{selected_currency}{r.Remaining:,.2f} left" if r.Remaining >= 0
                else f"{selected_currency}{-r.Remaining:,.2f} over"
            )
            c3.caption(f"{icon} {selected_currency}{r.Spent:,.2f} / {selected_currency}{r.Budget:,.2f} · {left_txt} ({r.Pct * 100:.0f}%)")

        st.markdown("#### Budget vs Actual")
        chart_df = budget_df.melt(id_vars="Category", value_vars=["Budget", "Spent"], var_name="Series", value_name="Amount")
        fig_budget = px.bar(
            chart_df,
            x="Category",
            y="Amount",
            color="Series",
            barmode="group",
            color_discrete_map={"Budget": "#94A3B8", "Spent": "#6366F1"},
            labels={"Amount": f"Amount ({selected_currency})"}
        )
        fig_budget.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig_budget.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#E2E8F0')
        st.plotly_chart(fig_budget, use_container_width=True)

# ---------------------------------------------------------------------
# TAB 3: LEDGER
# ---------------------------------------------------------------------
with tab_ledger:
    st.markdown("### 📜 Interactive Ledger History")

    if not df.empty:
        st.markdown("*💡 Tip: Double-click any cell to directly edit the values. Your updates save to the database in real-time!*")

        sorted_df = df.sort_values(by="Date", ascending=False).copy()
        sorted_df['Date'] = pd.to_datetime(sorted_df['Date'])

        edited_df = st.data_editor(
            sorted_df,
            column_config={
                "Amount": st.column_config.NumberColumn(f"Amount ({selected_currency})", format=f"{selected_currency}%.2f"),
                "Type": st.column_config.SelectboxColumn("Type", options=["Income", "Expense"]),
                "Category": st.column_config.SelectboxColumn("Category", options=sorted(set(CATEGORIES["Income"] + CATEGORIES["Expense"]))),
                "Notes": st.column_config.TextColumn("Description"),
                "Date": st.column_config.DateColumn("Date")
            },
            use_container_width=True,
            hide_index=True,
            key="ledger_data_editor"
        )

        if not edited_df.equals(sorted_df):
            for doc_id in edited_df.index:
                if not edited_df.loc[doc_id].equals(sorted_df.loc[doc_id]):
                    update_transaction_in_db(user_id, doc_id, edited_df.loc[doc_id])
            st.session_state.transactions = load_transactions(user_id)
            st.toast("Spreadsheet edits auto-saved!", icon="💾")
            st.rerun()

        st.markdown("---")
        st.markdown("### 🗑️ Delete a Transaction")

        transaction_to_delete = st.selectbox(
            "Select a transaction to permanently delete:",
            options=sorted_df.index,
            format_func=lambda idx: f"{sorted_df.loc[idx, 'Date'].strftime('%Y-%m-%d')} | {sorted_df.loc[idx, 'Category']} | {selected_currency}{sorted_df.loc[idx, 'Amount']:,.2f} | {sorted_df.loc[idx, 'Notes']}"
        )

        col_del1, col_del2 = st.columns(2)
        with col_del1:
            delete_btn = st.button("❌ Delete Transaction", type="secondary", use_container_width=True)

        if delete_btn:
            delete_transaction_from_db(user_id, transaction_to_delete)
            st.session_state.transactions = load_transactions(user_id)
            st.toast("Transaction successfully deleted!", icon="🗑️")
            st.rerun()

    else:
        if all_df.empty:
            st.warning("No recorded transaction history found.")
        else:
            st.warning("No transactions match the selected period/filters.")
