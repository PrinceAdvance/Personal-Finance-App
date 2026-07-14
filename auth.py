import streamlit as st


def login():
    """Simple username/password gate backed by st.secrets.

    Not bank-grade security, but appropriate for a personal app shared
    with a few people you trust. Each username becomes the Firestore
    document id under which that person's data is stored, so data is
    kept separate per user.
    """
    if "user" in st.session_state:
        return st.session_state["user"]

    st.title("🔒 Log in")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Log in", type="primary"):
        users = dict(st.secrets.get("users", {}))
        if username in users and users[username] == password:
            st.session_state["user"] = username
            st.rerun()
        else:
            st.error("Invalid username or password")

    st.stop()  # halts execution here until login succeeds


def logout_button():
    with st.sidebar:
        if st.button("Log out"):
            del st.session_state["user"]
            st.rerun()
