
# app.py — Streamlit UI for the Cosmetic Interpreter Executor (ported from notebook)
# Notes:
# - Reads cosmetics_library from cosmetics_library.json
# - Uses Firebase Admin (service account) to store user inventory in RTDB
# - For security, provide credentials via Streamlit Secrets. See README.md.

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Set, Tuple, Optional

import streamlit as st

# --- Optional: Firebase ---
try:
    import firebase_admin
    from firebase_admin import credentials, auth, db
    FIREBASE_AVAILABLE = True
except Exception:
    FIREBASE_AVAILABLE = False

# ============================
# Data models & parsing
# ============================
class TokenType(Enum):
    COMMAND = "COMMAND"
    STRING_LITERAL = "STRING_LITERAL"
    EOF = "EOF"

@dataclass
class Token:
    type: TokenType
    value: str
    position: int

@dataclass
class ASTNode:
    command: str
    arguments: List[str]

class ParseError(Exception):
    pass

class ExecutionError(Exception):
    pass

class CosmeticsTokenizer:
    COMMANDS = {
        "apply theme": TokenType.COMMAND,
        "add item": TokenType.COMMAND,
        "remove item": TokenType.COMMAND,
        "clear inventory": TokenType.COMMAND,
        "add item list": TokenType.COMMAND,
        "color palette": TokenType.COMMAND,
        "assemble cosmetic": TokenType.COMMAND,
        "register": TokenType.COMMAND,
        "login": TokenType.COMMAND,
        "logout": TokenType.COMMAND,
        "exit": TokenType.COMMAND,
    }

    def __init__(self, input_text: str):
        self.input = input_text.strip()
        self.tokens: List[Token] = []
        self.position = 0

    def tokenize(self) -> List[Token]:
        self.tokens = []
        self.position = 0

        cmd = self._match_command()
        if cmd:
            self.tokens.append(cmd)
            self._extract_string_literals()

        self.tokens.append(Token(TokenType.EOF, "", self.position))
        return self.tokens

    def _match_command(self) -> Optional[Token]:
        text = self.input.lower()
        for cmd in sorted(self.COMMANDS.keys(), key=len, reverse=True):
            if text.startswith(cmd):
                self.position = len(cmd)
                return Token(TokenType.COMMAND, cmd, 0)
        return None

    def _extract_string_literals(self):
        # Very simple quotes parser: everything in double quotes becomes a STRING_LITERAL
        text = self.input[self.position:].strip()
        i = 0
        while i < len(text):
            if text[i] == '"':
                j = i + 1
                while j < len(text) and text[j] != '"':
                    j += 1
                if j < len(text) and text[j] == '"':
                    value = text[i+1:j]
                    self.tokens.append(Token(TokenType.STRING_LITERAL, value, self.position + i))
                    i = j + 1
                else:
                    break
            else:
                i += 1

class CosmeticsParser:
    COMMAND_RULES = {
        "apply theme": (1, 1),
        "add item": (1, 1),
        "remove item": (1, 1),
        "clear inventory": (0, 0),
        "add item list": (1, 99),
        "color palette": (1, 99),
        "assemble cosmetic": (0, 0),
        "register": (2, 2),
        "login": (1, 1),
        "logout": (0, 0),
        "exit": (0, 0),
    }

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.i = 0

    def parse(self) -> ASTNode:
        if not self.tokens or self.tokens[0].type != TokenType.COMMAND:
            raise ParseError("Expected a command at the start.")
        cmd = self.tokens[0].value
        self.i = 1

        if cmd not in self.COMMAND_RULES:
            raise ParseError(f"Unknown command: {cmd}")

        args = []
        while self.i < len(self.tokens) and self.tokens[self.i].type == TokenType.STRING_LITERAL:
            args.append(self.tokens[self.i].value)
            self.i += 1

        min_a, max_a = self.COMMAND_RULES[cmd]
        if not (min_a <= len(args) <= max_a):
            raise ParseError(f"{cmd} expects {min_a}–{max_a} quoted argument(s). Got {len(args)}.")
        return ASTNode(command=cmd, arguments=args)

# ============================
# Domain models
# ============================
@dataclass
class CosmeticOutfit:
    name: str
    theme: str
    items: List[str]
    colors: List[str]
    image: str
    steps: List[str]

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CosmeticOutfit":
        return CosmeticOutfit(
            name=d.get("name", ""),
            theme=d.get("theme", ""),
            items=d.get("items", []),
            colors=d.get("colors", []),
            image=d.get("image", ""),
            steps=d.get("steps", []),
        )

@dataclass
class OutfitMatch:
    outfit: CosmeticOutfit
    missing_items: Set[str]
    missing_colors: Set[str]

# ============================
# Firebase manager
# ============================
class FirebaseManager:
    def __init__(self, cred_dict: Dict[str, Any], database_url: str):
        self.initialized = False
        try:
            cred = credentials.Certificate(cred_dict)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {"databaseURL": database_url})
            self.db_ref = db.reference()
            self.initialized = True
        except Exception as e:
            raise Exception(f"Firebase init failed: {e}")

    def register_user(self, email: str, password: str) -> Dict[str, Any]:
        try:
            user = auth.create_user(email=email, password=password)
            user_ref = self.db_ref.child("users").child(user.uid)
            user_ref.set({"email": email, "inventory": [], "created_at": st.session_state.now_iso})
            return {"success": True, "uid": user.uid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def login_user(self, email: str) -> Dict[str, Any]:
        # Admin SDK cannot verify passwords; this simplified "login" fetches by email.
        # For real auth, use Firebase Auth Client (JS) + custom tokens/backend.
        try:
            user = auth.get_user_by_email(email)
            return {"success": True, "uid": user.uid, "email": user.email}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_user_inventory(self, uid: str) -> List[str]:
        try:
            inv = self.db_ref.child("users").child(uid).child("inventory").get()
            return inv or []
        except Exception as e:
            st.warning(f"Error retrieving inventory: {e}")
            return []

    def save_user_inventory(self, uid: str, inventory: List[str]) -> bool:
        try:
            self.db_ref.child("users").child(uid).child("inventory").set(inventory)
            return True
        except Exception as e:
            st.error(f"Error saving inventory: {e}")
            return False

    def update_last_login(self, uid: str):
        try:
            self.db_ref.child("users").child(uid).child("last_login").set(st.session_state.now_iso)
        except Exception:
            pass

# ============================
# Executor
# ============================
class CosmeticsExecutor:
    def __init__(self, cosmetics_library: List[Dict[str, Any]], image_base_path: str = "cosmetics_images"):
        self.outfits = [CosmeticOutfit.from_dict(x) for x in cosmetics_library]
        self.image_base_path = image_base_path
        self.theme: Optional[str] = None
        self.inventory: Set[str] = set()
        self.palette: List[str] = []

    def execute(self, ast: ASTNode) -> str:
        cmd, args = ast.command, ast.arguments
        if cmd == "apply theme":
            return self._apply_theme(args[0])
        if cmd == "add item":
            return self._add_item(args[0])
        if cmd == "remove item":
            return self._remove_item(args[0])
        if cmd == "clear inventory":
            return self._clear_inventory()
        if cmd == "add item list":
            return self._add_item_list(args)
        if cmd == "color palette":
            return self._set_color_palette(args)
        if cmd == "assemble cosmetic":
            return self._assemble_cosmetic()
        raise ExecutionError(f"Unknown command: {cmd}")

    def _apply_theme(self, theme: str) -> str:
        self.theme = theme.lower()
        return f"✓ Theme applied: {theme}"

    def _add_item(self, item: str) -> str:
        item_l = item.lower()
        if item_l in self.inventory:
            return f"⚠ Item '{item}' is already in inventory"
        self.inventory.add(item_l)
        return f"✓ Added item: {item}"

    def _remove_item(self, item: str) -> str:
        item_l = item.lower()
        if item_l not in self.inventory:
            return f"⚠ Item '{item}' not in inventory"
        self.inventory.remove(item_l)
        return f"✓ Removed item: {item}"

    def _clear_inventory(self) -> str:
        self.inventory.clear()
        return "✓ Inventory cleared"

    def _add_item_list(self, items: List[str]) -> str:
        added = 0
        for it in items:
            if it.lower() not in self.inventory:
                self.inventory.add(it.lower())
                added += 1
        return f"✓ Added {added} items"

    def _set_color_palette(self, colors: List[str]) -> str:
        self.palette = [c.lower() for c in colors]
        return f"✓ Color palette set: {', '.join(self.palette)}"

    def _assemble_cosmetic(self) -> str:
        if not self.theme:
            return "✗ Error: No theme set. Use 'apply theme' first."
        if not self.inventory:
            return "✗ Error: Inventory is empty. Add items first."
        theme_outfits = [o for o in self.outfits if o.theme.lower() == self.theme]
        if not theme_outfits:
            return f"✗ Error: No outfits available for theme '{self.theme}'"

        exact, near = [], []
        inv = set(self.inventory)
        pal = set(self.palette) if self.palette else None

        for o in theme_outfits:
            items_set = set(x.lower() for x in o.items)
            colors_set = set(x.lower() for x in o.colors)
            missing_items = items_set - inv
            missing_colors = set() if pal is None else colors_set - pal
            if not missing_items and not missing_colors:
                exact.append(OutfitMatch(o, missing_items, missing_colors))
            else:
                near.append(OutfitMatch(o, missing_items, missing_colors))

        if exact:
            return f"✓ Assembled! Found {len(exact)} exact match(es)."
        if near:
            return f"~ Assembled partial: {len(near)} near match(es)."
        return "✗ No matches found for current theme and inventory."

    def get_matching_outfits(self) -> List[CosmeticOutfit]:
        if not self.theme:
            return []
        inv = set(self.inventory)
        pal = set(self.palette) if self.palette else None
        matches = []
        for o in self.outfits:
            if o.theme.lower() != self.theme:
                continue
            items_set = set(x.lower() for x in o.items)
            colors_set = set(x.lower() for x in o.colors)
            if items_set - inv:
                continue
            if pal is not None and (colors_set - pal):
                continue
            matches.append(o)
        return matches

# ============================
# Helpers
# ============================
def load_cosmetics_library(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def image_path_for(outfit: CosmeticOutfit, base: str) -> str:
    theme_folder = outfit.theme.replace(" ", "_")
    return os.path.join(base, theme_folder, outfit.image)

# ============================
# Streamlit UI
# ============================
st.set_page_config(page_title="Cosmetic Interpreter", page_icon="🧴", layout="wide")
if "now_iso" not in st.session_state:
    import datetime as _dt
    st.session_state.now_iso = _dt.datetime.now().isoformat()

st.title("🧴 Cosmetic Interpreter — Streamlit")

with st.sidebar:
    st.header("Authentication")

    firebase_enabled = st.checkbox("Enable Firebase (inventory sync)", value=False, help="Requires secrets configured.")
    firebase_mgr = None
    uid = st.session_state.get("uid")

    if firebase_enabled:
        if not FIREBASE_AVAILABLE:
            st.error("firebase-admin not installed.")
        else:
            try:
                # Try fetching secrets: st.secrets['firebase']['service_account'] or ['service_account_json']
                fb = st.secrets.get("firebase", {})
                database_url = fb.get("database_url", "")
                sa_json = fb.get("service_account", fb.get("service_account_json", None))
                if isinstance(sa_json, str):
                    # allow putting raw JSON string in secrets
                    sa = json.loads(sa_json)
                else:
                    sa = sa_json

                if not database_url or not sa:
                    raise RuntimeError("Missing firebase.database_url or firebase.service_account in secrets.")
                firebase_mgr = FirebaseManager(sa, database_url)
            except Exception as e:
                st.error(f"Firebase init error: {e}")
                firebase_enabled = False

    if firebase_enabled and firebase_mgr:
        if not uid:
            st.subheader("Login or Register")
            tab_login, tab_register = st.tabs(["Login (email only)", "Register (email+password)"])
            with tab_login:
                email = st.text_input("Email", key="login_email")
                if st.button("Login"):
                    res = firebase_mgr.login_user(email)
                    if res.get("success"):
                        st.session_state.uid = res["uid"]
                        st.session_state.email = res["email"]
                        firebase_mgr.update_last_login(res["uid"])
                        st.success("Logged in.")
                        st.rerun()
                    else:
                        st.error(res.get("error"))
            with tab_register:
                email2 = st.text_input("Email", key="reg_email")
                pw1 = st.text_input("Password", type="password", key="reg_pw")
                pw2 = st.text_input("Confirm Password", type="password", key="reg_pw2")
                if st.button("Register"):
                    if pw1 != pw2:
                        st.error("Passwords do not match.")
                    elif len(pw1) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        res = firebase_mgr.register_user(email2, pw1)
                        if res.get("success"):
                            st.success("Registration successful. Please login.")
                        else:
                            st.error(res.get("error"))
        else:
            st.write(f"**Logged in as:** {st.session_state.get('email', '(no email)')}")
            if st.button("Logout"):
                for k in ("uid", "email"):
                    st.session_state.pop(k, None)
                st.rerun()

st.divider()

# Library & executor
lib_path = "cosmetics_library.json"
if not os.path.exists(lib_path):
    st.error("cosmetics_library.json not found in app root.")
    st.stop()

library = load_cosmetics_library(lib_path)
executor = st.session_state.get("executor")
if executor is None:
    executor = CosmeticsExecutor(library, image_base_path="cosmetics_images")
    st.session_state.executor = executor

# Sync inventory from Firebase once per login
if firebase_enabled and firebase_mgr and st.session_state.get("uid") and not st.session_state.get("did_sync"):
    inv = firebase_mgr.get_user_inventory(st.session_state["uid"])
    executor.inventory = set([x.lower() for x in inv])
    st.session_state["did_sync"] = True

# Inventory editor
with st.expander("Inventory", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        add_item = st.text_input("Add item")
        if st.button("Add"):
            msg = executor._add_item(add_item)
            st.info(msg)
    with col2:
        remove_item = st.text_input("Remove item")
        if st.button("Remove"):
            msg = executor._remove_item(remove_item)
            st.info(msg)
    if st.button("Clear inventory"):
        st.info(executor._clear_inventory())

    st.write("**Current inventory:**", ", ".join(sorted(executor.inventory)) or "–")

# Theme & palette
with st.expander("Theme & Colors", expanded=True):
    theme = st.text_input("Theme (e.g., cyberpunk, dark fantasy, coquette)")
    if st.button("Apply theme"):
        st.info(executor._apply_theme(theme))

    palette = st.text_input('Color palette (comma-separated, e.g., "magenta, neon blue, black")')
    if st.button("Set palette"):
        colors = [c.strip() for c in palette.split(",") if c.strip()]
        st.info(executor._set_color_palette(colors))

# Command runner (optional power user mode)
with st.expander("Command Runner", expanded=False):
    cmd = st.text_input('Command (e.g., apply theme "cyberpunk", add item "jacket")')
    if st.button("Run command"):
        try:
            tokens = CosmeticsTokenizer(cmd).tokenize()
            ast = CosmeticsParser(tokens).parse()
            msg = executor.execute(ast)
            st.success(msg)
        except Exception as e:
            st.error(str(e))

# Assemble
st.subheader("Assemble Cosmetic")
if st.button("Assemble"):
    msg = executor._assemble_cosmetic()
    st.write(msg)

    # Save inventory if logged in
    if firebase_enabled and firebase_mgr and st.session_state.get("uid"):
        firebase_mgr.save_user_inventory(st.session_state["uid"], sorted(list(executor.inventory)))

    # Show matches with images and steps
    outfits = executor.get_matching_outfits()
    if outfits:
        for o in outfits:
            st.markdown(f"**{o.name}** · Theme: `{o.theme}` · Colors: {', '.join(o.colors)}")
            p = image_path_for(o, executor.image_base_path)
            if os.path.exists(p):
                st.image(p, caption=o.image)
            else:
                st.warning(f"Image not found: {p}")
            if o.steps:
                st.markdown("**Assembly Steps:**")
                for s in o.steps:
                    st.write("- " + s)
            st.divider()
    else:
        st.info("No matching outfits to display (try adjusting inventory, theme, or palette).")
