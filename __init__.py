import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import re

from aqt import mw
from aqt.utils import showInfo
from aqt.qt import *

# --- STANDARD CONFIG ---
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BATCH_SIZE = 10
MIN_CONTEXT_SIZE = 10
DEBUG_LOG = True

# ----------------- CONFIG MANAGER -----------------

def get_config():
    """Lädt die Konfiguration."""
    config = mw.addonManager.getConfig(__name__)
    if not config:
        config = {
            "provider": "openai",
            "openai_api_key": "",
            "gemini_api_key": "",
            "openai_model": "gpt-4o-mini",
            "gemini_model": "gemini-1.5-flash",
            "batch_size": 10
        }
    return config

def get_active_setup():
    """Ermittelt Provider, Key und Modell."""
    config = get_config()
    provider = config.get("provider", "openai").lower()
    
    if provider == "gemini":
        key = config.get("gemini_api_key", "").strip()
        model = config.get("gemini_model", "gemini-1.5-flash")
        label = "Google Gemini"
    else:
        key = config.get("openai_api_key", "").strip()
        if not key:
            key = config.get("api_key", "").strip()
        model = config.get("openai_model", "")
        if not model:
            model = config.get("model", DEFAULT_MODEL)
        label = "OpenAI"

    if not key:
        showInfo(f"{label} API Key missing.")
        return None, None, None
        
    return provider, key, model

# ----------------- HELPER FUNCTIONS -----------------

def strip_html(text: str) -> str:
    text = text.replace("<div>", " ").replace("<br>", " ").replace("&nbsp;", " ")
    try:
        if "<" in text:
            root = ET.fromstring(f"<span>{text}</span>")
            return "".join(root.itertext())
    except Exception:
        pass
    return text

def note_has_prio_tag(note) -> bool:
    return any(note.has_tag(t) for t in ["prio:1", "prio:2", "prio:3", "prio:4"])

def get_deck_name_for_note(nid: int) -> str:
    cids = mw.col.find_cards(f"nid:{nid}")
    if cids:
        card = mw.col.get_card(cids[0])
        did = card.did
    else:
        did = mw.col.decks.current()["id"]
    return mw.col.decks.name(did)

def group_notes_by_deck_hierarchy(nids):
    temp_groups = {}
    for nid in nids:
        deck_name = get_deck_name_for_note(nid)
        if deck_name not in temp_groups:
            temp_groups[deck_name] = []
        temp_groups[deck_name].append(nid)

    final_groups = {}
    sorted_deck_names = sorted(
        temp_groups.keys(), key=lambda x: x.count("::"), reverse=True
    )

    for name in sorted_deck_names:
        cards = temp_groups[name]
        if len(cards) >= MIN_CONTEXT_SIZE or "::" not in name:
            final_groups[name] = cards
        else:
            parent_name = "::".join(name.split("::")[:-1])
            while parent_name and parent_name not in temp_groups:
                parent_name = "::".join(parent_name.split("::")[:-1])

            if parent_name:
                temp_groups[parent_name].extend(cards)
            else:
                final_groups[name] = cards
    return final_groups

def choose_deck():
    # Sortierte Liste aller Decks
    all_decks = sorted([d["name"] for d in mw.col.decks.all()])
    if not all_decks:
        showInfo("No decks found.")
        return None

    # --- FEATURE: AKTUELLES DECK VORSELEKTIEREN ---
    try:
        current_did = mw.col.decks.current()['id']
        current_name = mw.col.decks.name(current_did)
        if current_name in all_decks:
            default_index = all_decks.index(current_name)
        else:
            default_index = 0
    except:
        default_index = 0
    # ----------------------------------------------

    deck_name, ok = QInputDialog.getItem(
        mw, "Choose Deck", "Which deck should be prioritized?",
        all_decks, default_index, False, 
    )
    if not ok or not deck_name:
        return None
    return deck_name

# ----------------- UI DIALOG -----------------

class ConfigDialog(QDialog):
    def __init__(self, parent, total_count, prio_count):
        super().__init__(parent)
        self.setWindowTitle("Prioritization Settings")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Found {total_count} cards. {prio_count} already have tags."))
        
        self.group = QButtonGroup(self)
        self.rb_skip = QRadioButton(f"Skip existing")
        self.rb_skip.setChecked(True)
        self.group.addButton(self.rb_skip)
        layout.addWidget(self.rb_skip)
        
        self.rb_overwrite = QRadioButton("Reprioritize ALL")
        self.group.addButton(self.rb_overwrite)
        layout.addWidget(self.rb_overwrite)
        
        layout.addWidget(QLabel("Custom Context (Optional):"))
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("e.g. 'Focus on Math Abitur'")
        layout.addWidget(self.prompt_input)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)

    def get_data(self):
        return {
            "skip_existing": self.rb_skip.isChecked(),
            "custom_prompt": self.prompt_input.text().strip()
        }

# ----------------- PROMPTS (DIE GUTE RUBRIC) -----------------

def get_system_prompt(deck_context_name, custom_user_instruction=""):
    custom_text = f"\nUSER FOCUS: {custom_user_instruction}\n" if custom_user_instruction else ""
    return f"""
You are an algorithmic grading system for flashcards. Subject: '{deck_context_name}'.
{custom_text}

STRICT SCORING RUBRIC (Do not deviate):
1 (HIGH): 
- DEFINITIONS of core terms.
- FUNDAMENTAL FORMULAS or LAWS.
- Key CAUSE-EFFECT relationships.
- Necessary for passing an exam.

2 (MEDIUM): 
- EXAMPLES or APPLICATION of formulas.
- DERIVATIONS of formulas.
- Specific PROPERTIES of concepts.
- Standard knowledge.

3 (LOW): 
- Historical dates / Trivia.
- Specific constants.
- Very specific niche details.

4 (UNNECESSARY): 
- Duplicate information.
- Meta-text.
- Editing artifacts.

Reply ONLY JSON: {{"ratings": [{{"id": 123456, "prio": 1}}]}}
"""

def prepare_cards_text(cards_data):
    lines = []
    for c in cards_data:
        q = (c["question"][:300] + "..") if len(c["question"]) > 300 else c["question"]
        a = (c["answer"][:300] + "..") if len(c["answer"]) > 300 else c["answer"]
        lines.append(f"ID: {c['id']} | Q: {q} | A: {a}")
    return "\n".join(lines)

# ----------------- API CALLS -----------------

def call_openai_batch(cards_data, deck_name, api_key, model, custom_instr):
    url = "https://api.openai.com/v1/chat/completions"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": get_system_prompt(deck_name, custom_instr)},
            {"role": "user", "content": prepare_cards_text(cards_data)},
        ],
        "response_format": {"type": "json_object"},
        # FIX: Seed und Temperature entfernt, da sie bei manchen Modellen Fehler 400 auslösen
    }

    if DEBUG_LOG: print(f"OpenAI Call: {len(cards_data)} cards")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return None

def call_gemini_batch(cards_data, deck_name, api_key, model, custom_instr):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    sys_prompt = get_system_prompt(deck_name, custom_instr)
    user_msg = prepare_cards_text(cards_data)
    full_text = f"{sys_prompt}\n\nDATA TO ANALYZE:\n{user_msg}"
    
    payload = {
        "contents": [{"parts": [{"text": full_text}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.0 # Gemini verträgt Temperature meistens
        }
    }

    if DEBUG_LOG: print(f"Gemini Call: {len(cards_data)} cards")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.load(response)
            if "candidates" in result and result["candidates"]:
                content = result["candidates"][0]["content"]["parts"][0]["text"]
                content = content.replace("```json", "").replace("```", "").strip()
                return json.loads(content)
            return None
    except Exception as e:
        print(f"Gemini Error: {e}")
        return None

# ----------------- APPLY PRIO -----------------

def apply_priority(ratings):
    if not ratings or "ratings" not in ratings: return 0
    cnt = 0
    for item in ratings["ratings"]:
        try:
            nid = item["id"]
            prio = item["prio"]
            if prio not in (1, 2, 3, 4): continue
            note = mw.col.get_note(nid)
            for t in ["prio:1", "prio:2", "prio:3", "prio:4"]:
                if note.has_tag(t): note.remove_tag(t)
            note.add_tag(f"prio:{prio}")
            mw.col.update_note(note)
            cnt += 1
        except: continue
    return cnt

# ----------------- MAIN LOOP -----------------

def on_prioritize_smart():
    # 1. Deck wählen (Mit Auto-Select)
    deck_name = choose_deck()
    if not deck_name: return

    # 2. Setup
    provider, api_key, model = get_active_setup()
    if not api_key: return

    search = f'deck:"{deck_name}"'
    nids = mw.col.find_notes(search)
    if not nids:
        showInfo(f"No cards found.")
        return

    prio_count = 0
    for nid in nids:
        if note_has_prio_tag(mw.col.get_note(nid)): prio_count += 1

    dialog = ConfigDialog(mw, len(nids), prio_count)
    if dialog.exec() != QDialog.DialogCode.Accepted: return

    user_settings = dialog.get_data()
    skip = user_settings["skip_existing"]
    custom = user_settings["custom_prompt"]

    mw.progress.start(label="Initializing...", max=len(nids))
    
    total_cards = len(nids)
    stats = {"seen": 0, "prio": 0, "skipped": 0}
    config = get_config()
    batch_size = config.get("batch_size", 10)
    
    try:
        groups = group_notes_by_deck_hierarchy(nids)

        for g_name, g_nids in groups.items():
            batch = []
            for nid in g_nids:
                stats["seen"] += 1
                note = mw.col.get_note(nid)
                label = f"Progress: {int((stats['seen']/total_cards)*100)}%"

                if skip and note_has_prio_tag(note):
                    stats["skipped"] += 1
                    mw.progress.update(value=stats["seen"], label=f"{label} | Skipping...")
                    continue

                q = strip_html(note.fields[0])
                a = strip_html(note.fields[1]) if len(note.fields) > 1 else ""
                batch.append({"id": nid, "question": q, "answer": a})

                if len(batch) >= batch_size:
                    mw.progress.update(value=stats["seen"], label=f"{label} | Calling {provider}...")
                    mw.app.processEvents()
                    
                    resp = None
                    if provider == "gemini":
                        resp = call_gemini_batch(batch, g_name, api_key, model, custom)
                    else:
                        resp = call_openai_batch(batch, g_name, api_key, model, custom)
                    
                    if resp: stats["prio"] += apply_priority(resp)
                    batch = []

            if batch:
                mw.progress.update(value=stats["seen"], label=f"{label} | Finishing...")
                mw.app.processEvents()
                
                resp = None
                if provider == "gemini":
                    resp = call_gemini_batch(batch, g_name, api_key, model, custom)
                else:
                    resp = call_openai_batch(batch, g_name, api_key, model, custom)
                
                if resp: stats["prio"] += apply_priority(resp)

    finally:
        mw.progress.finish()
        mw.reset()

    showInfo(
        f"Done using {provider}!\n"
        f"Model: {model}\n"
        f"{stats['prio']} newly prioritized.\n"
        f"{stats['skipped']} skipped."
    )

action = QAction("AI Prioritization", mw)
action.triggered.connect(on_prioritize_smart)
mw.form.menuTools.addAction(action)
